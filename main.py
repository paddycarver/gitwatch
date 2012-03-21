from google.appengine.api import channel
from google.appengine.api import memcache
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from django.utils import simplejson
import time
import logging
import httplib

class MainPage(webapp.RequestHandler):
        def get(self):
                now = time.time()
                req_id = str(now) + self.request.remote_addr
                req_id = req_id[0:63]
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

application = webapp.WSGIApplication([
        ('/', MainPage)
        ])

def main():
        run_wsgi_app(application)

if __name__ == "__main__":
        main()
