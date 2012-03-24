from google.appengine.api import channel
from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.ext import db
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from django.utils import simplejson
import time
from datetime import datetime, timedelta
import logging
import re
import hashlib
import unicodedata

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
        owner_hash = db.StringProperty()
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
                owner_hash = hashlib.md5(json["owner"]["email"].strip().lower()).hexdigest()                
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
                                private=private, owner_hash=owner_hash)
                return repo

class Commit(db.Model):
        id = db.StringProperty(required=True)
        url = db.StringProperty(required=True)
        author_name = db.StringProperty(required=True)
        author_email = db.StringProperty(required=True)
        author_hash = db.StringProperty()
        pusher = db.StringProperty()
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
                pusher = None
                if "pusher" in json:
                        if "name" in json["pusher"]:
                                pusher = json["pusher"]["name"]
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
                                repository=repo, author_hash=author_hash,
                                pusher=pusher)
                return commit

class GlobalMetric(db.Model):
        nature = db.StringProperty() # commit or curse
        count = db.IntegerProperty()
       
class RepoMetric(db.Model):
        url = db.StringProperty()
        count = db.IntegerProperty()
        nature = db.StringProperty() # commit or curse

class AuthorMetric(db.Model):
        email = db.StringProperty()
        name = db.StringProperty()
        count = db.IntegerProperty()
        nature = db.StringProperty() # commit or curse
        repometric = db.ReferenceProperty(RepoMetric, collection_name="authors")

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
                
                commits = Commit.all().order("-timestamp").fetch(1000)
                approved_commits = []
                for commit in commits:
                        if commit.repository.approved:
                                approved_commits.append(commit)
                        if len(approved_commits) > 9:
                                break
                global_commits = 0
                global_curses = 0
                commit_query = GlobalMetric.all().filter("nature = ", "commit").get()
                if commit_query:
                        global_commits = commit_query.count

                curse_query = GlobalMetric.all().filter("nature = ", "curse").get()
                if curse_query:
                        global_curses = curse_query.count

                authors_desc = []
                authors_asc = []
                repos_desc = []
                repos_asc = []
                authors_d = AuthorMetric.all().filter("nature =", "commit").order("-count").fetch(1000)
                authors_a = AuthorMetric.all().filter("nature =", "commit").order("count").fetch(10)
                repos_d = RepoMetric.all().filter("nature =", "commit").order("-count").fetch(1000)
                repos_a = RepoMetric.all().filter("nature =", "commit").order("count").fetch(10)
                for author in authors_d:
                        authors_desc.append({"count": author.count, "name": author.name, "email": author.email})
                for author in authors_a:
                        authors_asc.append({"count": author.count, "name": author.name, "email": author.email})
                for repo in repos_d:
                        repos_desc.append({"count": repo.count, "url": repo.url, "name": repo.url.split("/")[-1]})
                for repo in repos_a:
                        repos_asc.append({"count": repo.count, "url": repo.url, "name": repo.url.split("/")[-1]})
                
                template_values = {"token": new_token, "page": "main", "authors_desc": authors_desc, "authors_asc": authors_asc, "repos_asc": repos_asc, "repos_desc": repos_desc, "commits": approved_commits, "global_commit_count": global_commits, "global_curse_count": global_curses}

                self.response.out.write(template.render('index.html', template_values))

class AdminPage(webapp.RequestHandler):
        def get(self):
                user = users.get_current_user()
                if not user:
                        self.redirect(users.create_login_url("/admin"))
                        return
                if not users.is_current_user_admin():
                        self.redirect("/")
                        return
                repos = Repository.all().filter("approved = ", False).fetch(1000)
                self.response.out.write(template.render("index.html", {"page": "admin", "repos": repos}))

class ApproveRepo(webapp.RequestHandler):
        def post(self, repo_key):
                logging.info(repo_key)
                repo = Repository.get(db.Key(repo_key))
                repo.approved = True
                repo.put()


class HookReceiver(webapp.RequestHandler):
        def post(self):
                logging.info(self.request.body)
                logging.info(self.request.get("payload"))
                body = simplejson.loads(self.request.get("payload"))
                repository = Repository.all().filter("url =", body["repository"]["url"]).get()
                if not repository:
                        repository = Repository.fromJSON(body["repository"])
                        repository.put()
                for commit in body["commits"]:
                        commit['pusher'] = body['pusher']
                        cmt = Commit.fromJSON(repository, commit)
                        cmt.put()
                        repository.last_update = datetime.now()
                        repository.put()
                        taskqueue.add(url="/metric", params={"id": cmt.id, "author_email": cmt.author_email, "author_name": cmt.author_name, "repo": cmt.repository.url, 
                                "message": cmt.message})
                        c = {
                                        "id": cmt.id,
                                        "url": cmt.url,
                                        "author_name": cmt.author_name,
                                        "author_hash": cmt.author_hash,
                                        "timestamp": cmt.timestamp,
                                        "message": cmt.summary,
                                        "repo_name": cmt.repository.name,
                                        "repo_url": cmt.repository.url,
                                        "pusher": cmt.pusher,
                                        "origin": "commit"
                                }
                        taskqueue.add(url="/pusher", params=c) 

class PushWorker(webapp.RequestHandler):
        def post(self):
                origin = self.request.get("origin")
                u = None
                if origin == "commit":
                        id = self.request.get("id")
                        url = self.request.get("url")
                        author_name = self.request.get("author_name")
                        author_hash = self.request.get("author_hash")
                        timestamp = self.request.get("timestamp")
                        message = self.request.get("message")
                        repo_name = self.request.get("repo_name")
                        repo_url = self.request.get("repo_url")
                        pusher = self.request.get("pusher")
                
                        u = {
                                "nature": "commit",
                                "id": id,
                                "url": url,
                                "author_name": author_name,
                                "author_hash": author_hash,
                                "timestamp": timestamp,
                                "message": message,
                                "repo_name": repo_name,
                                "pusher": pusher,
                                "repo_url": repo_url
                        }
                elif origin == "metrics":
                        authors_desc = []
                        authors_asc = []
                        repos_desc = []
                        repos_asc = []
                        authors_d = AuthorMetric.all().filter("nature =", "commit").order("-count").fetch(10)
                        authors_a = AuthorMetric.all().filter("nature =", "commit").order("count").fetch(10)
                        repos_d = RepoMetric.all().filter("nature =", "commit").order("-count").fetch(10)
                        repos_a = RepoMetric.all().filter("nature =", "commit").order("count").fetch(10)
                        for author in authors_d:
                                authors_desc.append({"count": author.count, "name": author.name})
                        for author in authors_a:
                                authors_asc.append({"count": author.count, "name": author.name})
                        for repo in repos_d:
                                repos_desc.append({"count": repo.count, "url": repo.url})
                        for repo in repos_a:
                                repos_asc.append({"count": repo.count, "url": repo.url})
                        u = {
                                "nature": "metrics",
                                "global_commits": self.request.get("global_commits"),
                                "global_curses": self.request.get("global_curses"),
                                "author": {
                                        "desc": authors_desc,
                                        "asc": authors_asc
                                },
                                "repo": {
                                        "desc": repos_desc,
                                        "asc": repos_asc
                                }
                        }
                if u is not None:
                        tokens = memcache.get("tokens")
                        valid_tokens = []
                        if tokens is None:
                                tokens = []
                        for id, token, expiration in tokens:
                                if expiration > time.time():
                                        valid_tokens.append((id, token, expiration))
                                        channel.send_message(id, simplejson.dumps(u))
                        memcache.set("tokens", valid_tokens)

class AwardsWorker(webapp.RequestHandler):
        def post(self):
                global_commits = self.request.get("global_commits")
                author_name = self.request.get("author_name")
                author_email = self.request.get("author_email")

                if global_commits == 100:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 100th Commit", "%s (%s) deserves a prize for the 100th commit tonight." % (author_name, author_email))
                elif global_commits == 150:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 150th Commit", "%s (%s) deserves a prize for the 150th commit tonight." % (author_name, author_email))
                elif global_commits == 200:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 200th Commit", "%s (%s) deserves a prize for the 200th commit tonight." % (author_name, author_email))
                elif global_commits == 250:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 250th Commit", "%s (%s) deserves a prize for the 250th commit tonight." % (author_name, author_email))
                elif global_commits == 500:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 500th Commit", "%s (%s) deserves a prize for the 500th commit tonight." % (author_name, author_email))
                elif global_commits == 750:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 750th Commit", "%s (%s) deserves a prize for the 750th commit tonight." % (author_name, author_email))
                elif global_commits == 1000:
                        mail.send_mail_to_admins("nvdirienzo@gmail.com", "UB Hacking 1000th Commit", "%s (%s) deserves a prize for the 1000th commit tonight." % (author_name, author_email))

class MetricWorker(webapp.RequestHandler):
        def post(self):
                total_curses_used = 0 

                commit_id = self.request.get("id")
                author_email = self.request.get("author_email")
                author_name = self.request.get("author_name")
                repo = self.request.get("repo")
                message = self.request.get("message")
                r = re.compile("[^\w]ass[^\w]|[^\w]asshole[^\w]|[^\w]dumbass[^\w]|[^\w]hell[^\w]|fuck|shit|damn|bitch|bastard", flags=re.IGNORECASE)
                found_words = r.findall(message)
                total_curses_used = len(found_words)

                updated_entries = []

                if total_curses_used > 0:
                        cmt = Commit.all().filter("id =", commit_id).get()
                        cmt.num_curses = total_curses_used
                        updated_entries.append(cmt)

                query = GlobalMetric.all().filter("nature = ", "commit").get()
                if not query:
                        query = GlobalMetric(nature="commit", count=1)
                else:
                        query.count += 1
                updated_entries.append(query)
                global_commits = query.count

                query = GlobalMetric.all().filter("nature = ", "curse").get()
                if not query:
                        query = GlobalMetric(nature="curse", count=total_curses_used)
                else:
                        query.count += total_curses_used
                updated_entries.append(query)
                global_curses = query.count

                repo_commit_query = RepoMetric.all().filter("nature = ", "commit").filter("url = ", repo).get()
                if not repo_commit_query:
                        repo_commit_query = RepoMetric(url=repo, count=1, nature="commit")
                else:
                        repo_commit_query.count += 1
                repo_commit_query.put()

                repo_curse_query = RepoMetric.all().filter("nature = ", "curse").filter("url = ", repo).get()
                if not repo_curse_query:
                        repo_curse_query = RepoMetric(url=repo, count=total_curses_used, nature="curse")
                else:
                        repo_curse_query.count += total_curses_used
                repo_curse_query.put()

                query = AuthorMetric.all().filter("nature = ", "commit").filter("email = ", author_email).get()
                if not query:
                        query = AuthorMetric(email=author_email, name=author_name, count=1, nature="commit", repometric=repo_commit_query)
                else:
                        query.count += 1
                updated_entries.append(query)

                query = AuthorMetric.all().filter("nature = ", "curse").filter("email = ", author_email).get()
                if not query:
                        query = AuthorMetric(email=author_email, name=author_name, count=total_curses_used, nature="curse", repometric=repo_curse_query)
                else:
                        query.count += total_curses_used
                updated_entries.append(query)


                mets = {
                        "origin": "metrics",
                        "global_commits": global_commits,
                        "global_curses": global_curses,
                        "author_name": author_name,
                        "author_email": author_email
                }

                db.put(updated_entries)
                taskqueue.add(url="/pusher", params=mets)
                taskqueue.add(url="/awards", params=mets)


application = webapp.WSGIApplication([
        ('/metric', MetricWorker),
        ('/pusher', PushWorker),
        ('/github', HookReceiver),
        ('/admin', AdminPage),
        ('/approve/([^/]+)', ApproveRepo),
        ('/', MainPage)
        ])

def main():
        run_wsgi_app(application)

if __name__ == "__main__":
        main()
