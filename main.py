from google.appengine.api import channel
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import webapp
from google.appengine.ext import db
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from django.utils import simplejson
import time
from datetime import datetime, timedelta
import logging
import hashlib

curses = ["fuck", "hell", "ass", "damn", "bitch", "shit", "crap", "suck", "piss"]

class MissingParamException(Exception):
        param = None

        def __init__(self, param):
                self.param = param

        def __str__(self):
                return ("MissingParamException: %s", self.param)

class Repository(db.Model):
        url = db.StringProperty(required=True)
        name = db.StringProperty(required=True)
        forks = db.IntegerProperty(required=True)        
        watchers = db.IntegerProperty(required=True)
        owner_name = db.StringProperty(required=True)
        owner_email = db.StringProperty(required=True)
        description = db.StringProperty(required=False)
        private = db.BooleanProperty(default=False)
        approved = db.BooleanProperty(default=False)
        last_update = db.DateTimeProperty(auto_now=True)
        first_seen = db.DateTimeProperty(auto_now_add=True)

        @staticmethod
        def fromJSON(json):
                if "url" not in json:
                        raise MissingParamException("url")
                url = json["url"]
                if "owner" not in json:
                        raise MissingParamException("owner")
                if "email" not in json["owner"]:
                        raise MissingParamException("owner.email")
                if "name" not in json["owner"]:
                        raise MissingParamException("owner.name")
                owner_email = json["owner"]["email"]
                owner_name = json["owner"]["name"]
                name = url.split("/")[-1]
                if "name" in json:
                        name = json["name"]
                forks = 0
                if "forks" in json:
                        forks = json["forks"]
                watchers = 0
                if "watchers" in json:
                        watchers = json["watchers"]
                description = None
                if "description" in json:
                        description = json["description"]
                private = False
                if "private" in json:
                        private = json["private"] == 1
                repo = Repository(url=url, owner_email=owner_email,
                                owner_name=owner_name, name=name, forks=forks, 
                                watchers=watchers, description=description,
                                private=private)
                return repo

class Commit(db.Model):
        id = db.StringProperty(required=True)
        url = db.StringProperty(required=True)
        author_name = db.StringProperty(required=True)
        author_email = db.StringProperty(required=True)
        author_hash = db.StringProperty()
        timestamp = db.DateTimeProperty()
        message = db.TextProperty()
        summary = db.StringProperty()
        added = db.StringListProperty()
        repository = db.ReferenceProperty(Repository, collection_name="commits")
        num_curses = db.IntegerProperty(default=0)

        @staticmethod
        def fromJSON(repo, json):
                if "id" not in json:
                        raise MissingParamException("id")
                id = json["id"]
                if "url" not in json:
                        raise MissingParamException("url")
                url = json["url"]
                if "author" not in json:
                        raise MissingParamException("author")
                if "email" not in json["author"]:
                        raise MissingParamException("author.email")
                if "name" not in json["author"]:
                        raise MissingParamException("author.name")
                author_name = json["author"]["name"]
                author_email = json["author"]["email"]
                author_hash = hashlib.md5(json["author"]["email"].strip().lower()).hexdigest()
                timestamp = datetime.now()
                if "timestamp" in json:
                        offset = None
                        if json["timestamp"].rindex("-") > json["timestamp"].index("T"):
                                offset = ("-", json["timestamp"].rsplit("-", 1)[-1])
                                json["timestamp"] = json["timestamp"].rsplit(
                                                "-", 1)[0]
                        if "+" in json["timestamp"]:
                                offset = ("+", json["timestamp"].split("+")[-1])
                                json["timestamp"] = json["timestamp"].split(
                                                "+")[0]
                        timestamp = datetime.strptime(json["timestamp"],
                                "%Y-%m-%dT%H:%M:%S")
                        hours = int(offset[1].split(":")[0])
                        minutes = int(offset[1].split(":")[1])
                        if offset[0] == "+":
                                timestamp = timestamp + timedelta(hours=hours, 
                                                minutes=minutes)
                        else:
                                timestamp = timestamp - timedelta(minutes=minutes,
                                                hours=hours)
                message = None
                summary = None
                if "message" in json:
                        message = json["message"]
                        summary = json["message"][0:139]
                added = []
                if "added" in json:
                        added = json["added"]
                commit = Commit(id=id, url=url, author_name=author_name,
                                author_email=author_email, timestamp=timestamp,
                                message=message, summary=summary, added=added,
                                repository=repo)
                return commit

class Metric(db.Model):
        id = db.StringProperty()
        count = db.IntegerProperty()

class MainPage(webapp.RequestHandler):
        def get(self):
                now = time.time()
                req_id = str(now) + self.request.remote_addr
                req_id = req_id[0:63] # Generate a pseudo-unique string to use
                                      # as the channel ID
                new_token = channel.create_channel(req_id)
                expires = now + (60 * 60 * 2)
                tokens = memcache.get("tokens")
                valid_tokens = []
                if tokens is None:
                        tokens = []
                for id, token, expiration in tokens:
                        logging.info(id)
                        logging.info(token)
                        logging.info(expiration)
                        if expiration > time.time():
                                valid_tokens.append((id, token, expiration))
                valid_tokens.append((req_id, new_token, expires))
                memcache.set("tokens", valid_tokens)

                template_values = {"token": new_token}

                self.response.out.write(template.render('index.html', template_values))

class HookReceiver(webapp.RequestHandler):
        def post(self):
                body = simplejson.loads(self.request.body)
                repository = Repository.all().filter("url =", body["repository"]["url"]).get()
                if not repository:
                        repository = Repository.fromJSON(body["repository"])
                        repository.put()
                for commit in body["commits"]:
                        cmt = Commit.fromJSON(repository, commit)
                        taskqueue.add(url="/metric", params={"id": cmt.id, "author_email": cmt.author_email, "repo": cmt.repository.url, 
                                "num_curses": cmt.num_curses, "message": cmt.message})
                        cmt.put()
                        repository.last_update = datetime.now()
                        repository.put()

class MetricWorker(webapp.RequestHandler):
        def post(self):
                curses_used = {} # will be dynamically filled with the curses used
                total_curses_used = 0 

                commit_id = self.request.get("id")
                author_email = self.request.get("author_email")
                repo = self.request.get("repo")
                num_curses = self.request.get("num_curses")
                message = self.request.get("message")

                for curse in curses:
                        if curse in message:
                                curses_used[curse] = message.count(curse)
                                total_curses_used += 1

                if total_curses_used > 0:
                        Commit.all().filter("id = ", commit_id).get().num_curses = total_curses_used

                keys_to_check = ["commits_global", "commits_author_%s" % author_email, "commits_repo_%s" % repo,
                                "curses_global", "curses_author_%s" % author_email, "curses_repo_%s" % repo]

                updated_entries = []
                for key in keys_to_check:
                        if not memcache.get(key):
                                entry = Metric.all().filter("id = ", key).get()
                        if "commit" in key:
                                if not entry:
                                        entry = Metric(id=key, count=1)
                                else:
                                        entry.count += 1
                        elif "curse" in key:
                                if not entry:
                                        entry = Metric(id=key, count=total_curses_used)
                                else:
                                        entry.count += total_curses_used
                        updated_entries.append(entry)
                        memcache.set(key, entry)

                for curse in curses_used: # Individual curse word metrics
                        if not memcache.get("%s_global" % curse):
                                global_curse_entry = Metric.all().filter("id = ", "%s_global" % curse).get()
                        if not global_curse_entry:
                                global_curse_entry = Metric(id="%s_global" % curse, count=curses_used[curse])
                        else:
                                global_curse_entry.count += curses_used[curse]
                        updated_entries.append(global_curse_entry)
                        memcache.set("%s_global" % curse, global_curse_entry)

                        if not memcache.get("%s_author_%s" % (curse, author_email)):
                                author_curse_entry = Metric.all().filter("id = ", "%s_author_%s" % (curse, author_email)).get()
                        if not author_curse_entry:
                                author_curse_entry = Metric(id="%s_author_%s" % (curse, author_email), count=curses_used[curse])
                        else:
                                author_curse_entry.count += curses_used[curse]
                        updated_entries.append(author_curse_entry)
                        memcache.set("%s_author_%s" % (curse, author_email), author_curse_entry)

                        if not memcache.get("%s_repo_%s" % (curse, repo)):
                                repo_curse_entry = Metric.all().filter("id = ", "%s_repo_%s" % (curse, repo)).get()
                        if not repo_curse_entry:
                                repo_curse_entry = Metric(id="%s_repo_%s" % (curse, repo), count=curses_used[curse])
                        else:
                                repo_curse_entry.count += curses_used[curse]
                        updated_entries.append(repo_curse_entry)
                        memcache.set("%s_repo_%s" % (curse, repo), repo_curse_entry)

                db.put(updated_entries)


application = webapp.WSGIApplication([
        ('/metric', MetricWorker),
        ('/github', HookReceiver),
        ('/', MainPage)
        ])

def main():
        run_wsgi_app(application)

if __name__ == "__main__":
        main()
