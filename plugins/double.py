from plugin import BasePlugin

class Plugin(BasePlugin):
    def init(self):
        self.add_event_handler('muc_say', self.double)

    def double(self, msg, tab):
        split = msg['body'].split()
        if split:
            msg['body'] = split[0] + ' ' + msg['body']
