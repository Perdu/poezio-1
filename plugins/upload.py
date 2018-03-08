"""
Upload a file and auto-complete the input with its URL.

Usage
-----

This plugin adds a command to the chat tabs.

.. glossary::

    /upload
        **Usage:** ``/upload <filename>``

        Uploads the <filename> file to the preferred HTTP File Upload
        service (see XEP-0363) and fill the input with its URL.


"""
import asyncio
from os.path import expanduser
from glob import glob

from poezio.plugin import BasePlugin
from poezio.core.structs import Completion
from poezio.decorators import command_args_parser
from poezio import tabs

class Plugin(BasePlugin):

    def init(self):
        for _class in (tabs.PrivateTab, tabs.ConversationTab, tabs.MucTab):
            self.api.add_tab_command(_class, 'upload', self.command_upload,
                    usage='<filename>',
                    help='Upload a file and auto-complete the input with its URL.',
                    short='Upload a file',
                    completion=self.completion_filename)

    @asyncio.coroutine
    def async_upload(self, filename):
        try:
            url = yield from self.core.xmpp['xep_0363'].upload_file(filename)
        except Exception as e:
            self.api.information('Failed to upload the file: %s(%s)' % (type(e), e), 'Error')
        self.core.insert_input_text(url)

    @command_args_parser.quoted(1)
    def command_upload(self, args):
        if args is None:
            self.core.command.help('upload')
            return
        filename, = args
        filename = expanduser(filename)
        asyncio.ensure_future(self.async_upload(filename))

    @staticmethod
    def completion_filename(the_input):
        txt = expanduser(the_input.get_text()[8:])
        files = glob(txt + '*')
        return Completion(the_input.auto_completion, files, quotify=False)
