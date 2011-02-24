# Copyright 2010-2011 Le Coz Florent <louiz@louiz.org>
#
# This file is part of Poezio.
#
# Poezio is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# Poezio is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Poezio.  If not, see <http://www.gnu.org/licenses/>.

from gettext import (bindtextdomain, textdomain, bind_textdomain_codeset,
                     gettext as _)
from os.path import isfile

from time import sleep

import os
import re
import sys
import curses
import threading
import traceback

from datetime import datetime

import common
import theme
import logging

from sleekxmpp.xmlstream.stanzabase import JID

log = logging.getLogger(__name__)

import multiuserchat as muc
import tabs
import windows

from data_forms import DataFormsTab
from connection import connection
from config import config
from logger import logger
from user import User
from room import Room
from roster import Roster, RosterGroup, roster
from contact import Contact, Resource
from text_buffer import TextBuffer
from keyboard import read_char

# http://xmpp.org/extensions/xep-0045.html#errorstatus
ERROR_AND_STATUS_CODES = {
    '401': _('A password is required'),
    '403': _('Permission denied'),
    '404': _('The room does\'nt exist'),
    '405': _('Your are not allowed to create a new room'),
    '406': _('A reserved nick must be used'),
    '407': _('You are not in the member list'),
    '409': _('This nickname is already in use or has been reserved'),
    '503': _('The maximum number of users has been reached'),
    }

SHOW_NAME = {
    'dnd': _('busy'),
    'away': _('away'),
    'xa': _('not available'),
    'chat': _('chatty'),
    '': _('available')
    }

possible_show = {'available':None,
                 'chat':'chat',
                 'away':'away',
                 'afk':'away',
                 'dnd':'dnd',
                 'busy':'dnd',
                 'xa':'xa'
                 }

resize_lock = threading.Lock()

class Core(object):
    """
    User interface using ncurses
    """
    def __init__(self, xmpp):
        # All uncaught exception are given to this callback, instead
        # of being displayed on the screen and exiting the program.
        sys.excepthook = self.on_exception
        self.running = True
        self.stdscr = curses.initscr()
        self.init_curses(self.stdscr)
        self.xmpp = xmpp
        # a unique buffer used to store global informations
        # that are displayed in almost all tabs, in an
        # information window.
        self.information_buffer = TextBuffer()
        self.information_win_size = config.get('info_win_height', 2, 'var')
        default_tab = tabs.InfoTab(self) if self.xmpp.anon\
            else tabs.RosterInfoTab(self)
        default_tab.on_gain_focus()
        self.tabs = [default_tab]
        self.previous_tab_nb = 0
        self.own_nick = config.get('own_nick', '') or self.xmpp.boundjid.user
        # global commands, available from all tabs
        # a command is tuple of the form:
        # (the function executing the command. Takes a string as argument,
        #  a string representing the help message,
        #  a completion function, taking a Input as argument. Can be None)
        #  The completion function should return True if a completion was
        #  made ; False otherwise
        self.commands = {
            'help': (self.command_help, '\_o< KOIN KOIN KOIN', self.completion_help),
            'join': (self.command_join, _("Usage: /join [room_name][@server][/nick] [password]\nJoin: Join the specified room. You can specify a nickname after a slash (/). If no nickname is specified, you will use the default_nick in the configuration file. You can omit the room name: you will then join the room you\'re looking at (useful if you were kicked). You can also provide a room_name without specifying a server, the server of the room you're currently in will be used. You can also provide a password to join the room.\nExamples:\n/join room@server.tld\n/join room@server.tld/John\n/join room2\n/join /me_again\n/join\n/join room@server.tld/my_nick password\n/join / password"), self.completion_join),
            'exit': (self.command_quit, _("Usage: /exit\nExit: Just disconnect from the server and exit poezio."), None),
            'next': (self.rotate_rooms_right, _("Usage: /next\nNext: Go to the next room."), None),
            'prev': (self.rotate_rooms_left, _("Usage: /prev\nPrev: Go to the previous room."), None),
            'win': (self.command_win, _("Usage: /win <number>\nWin: Go to the specified room."), self.completion_win),
            'w': (self.command_win, _("Usage: /w <number>\nW: Go to the specified room."), self.completion_win),
            'show': (self.command_show, _("Usage: /show <availability> [status]\nShow: Change your availability and (optionaly) your status, but only in the MUCs. This doesn’t affect the way your contacts will see you in their roster. The <availability> argument is one of \"available, chat, away, afk, dnd, busy, xa\" and the optional [status] argument will be your status message"), self.completion_show),
            'away': (self.command_away, _("Usage: /away [message]\nAway: Sets your availability to away and (optional) sets your status message. This is equivalent to '/show away [message]'"), None),
            'busy': (self.command_busy, _("Usage: /busy [message]\nBusy: Sets your availability to busy and (optional) sets your status message. This is equivalent to '/show busy [message]'"), None),
            'avail': (self.command_avail, _("Usage: /avail [message]\nAvail: Sets your availability to available and (optional) sets your status message. This is equivalent to '/show available [message]'"), None),
            'available': (self.command_avail, _("Usage: /available [message]\nAvailable: Sets your availability to available and (optional) sets your status message. This is equivalent to '/show available [message]'"), None),
           'bookmark': (self.command_bookmark, _("Usage: /bookmark [roomname][/nick]\nBookmark: Bookmark the specified room (you will then auto-join it on each poezio start). This commands uses the same syntaxe as /join. Type /help join for syntaxe examples. Note that when typing \"/bookmark\" on its own, the room will be bookmarked with the nickname you\'re currently using in this room (instead of default_nick)"), None),
            'set': (self.command_set, _("Usage: /set <option> [value]\nSet: Sets the value to the option in your configuration file. You can, for example, change your default nickname by doing `/set default_nick toto` or your resource with `/set resource blabla`. You can also set an empty value (nothing) by providing no [value] after <option>."), None),
            'theme': (self.command_theme, _('Usage: /theme\nTheme: Reload the theme defined in the config file.'), None),
            'list': (self.command_list, _('Usage: /list\n/List: get the list of public chatrooms on the specified server'), self.completion_list),
            'status': (self.command_status, _('Usage: /status <availability> [status message]\n/Status: Globally change your status and your status message.'), self.completion_status),
            'message': (self.command_message, _('Usage: /message <jid> [optional message]\n/Message: Open a conversation with the specified JID (even if it is not in our roster), and send a message to it, if specified'), None),
            }

        self.key_func = {
            "KEY_PPAGE": self.scroll_page_up,
            "KEY_NPAGE": self.scroll_page_down,
            "KEY_F(5)": self.rotate_rooms_left,
            "M-[1;6D": self.rotate_rooms_left,
            "^P": self.rotate_rooms_left,
            'kLFT3': self.rotate_rooms_left,
            "KEY_F(6)": self.rotate_rooms_right,
            "M-[1;6C": self.rotate_rooms_right,
            "^N": self.rotate_rooms_right,
            'kRIT3': self.rotate_rooms_right,
            "KEY_F(7)": self.shrink_information_win,
            "KEY_F(8)": self.grow_information_win,
            "KEY_RESIZE": self.call_for_resize,
            'M-e': self.go_to_important_room,
            'M-r': self.go_to_roster,
            'M-z': self.go_to_previous_tab,
            'M-v': self.move_separator,
            '^L': self.full_screen_redraw,
            }

        # Add handlers
        self.xmpp.add_event_handler('connected', self.on_connected)
        self.xmpp.add_event_handler('disconnected', self.on_disconnected)
        self.xmpp.add_event_handler('failed_auth', self.on_failed_auth)
        self.xmpp.add_event_handler("session_start", self.on_session_start)
        self.xmpp.add_event_handler("groupchat_presence", self.on_groupchat_presence)
        self.xmpp.add_event_handler("groupchat_message", self.on_groupchat_message)
        self.xmpp.add_event_handler("groupchat_subject", self.on_groupchat_subject)
        self.xmpp.add_event_handler("message", self.on_message)
        self.xmpp.add_event_handler("got_online" , self.on_got_online)
        self.xmpp.add_event_handler("got_offline" , self.on_got_offline)
        self.xmpp.add_event_handler("roster_update", self.on_roster_update)
        self.xmpp.add_event_handler("changed_status", self.on_presence)
        self.xmpp.add_event_handler("changed_subscription", self.on_changed_subscription)
        self.xmpp.add_event_handler("message_xform", self.on_data_form)
        self.xmpp.add_event_handler("chatstate_active", self.on_chatstate_active)
        self.xmpp.add_event_handler("chatstate_composing", self.on_chatstate_composing)
        self.xmpp.add_event_handler("chatstate_paused", self.on_chatstate_paused)
        self.xmpp.add_event_handler("chatstate_gone", self.on_chatstate_gone)
        self.xmpp.add_event_handler("chatstate_inactive", self.on_chatstate_inactive)
        self.information(_('Welcome to poezio!'))
        self.refresh_window()

    def on_exception(self, typ, value, trace):
        """
        When an exception in raised, open a special tab
        displaying the traceback and some instructions to
        make a bug report.
        """
        try:
            tb_tab = tabs.SimpleTextTab(self, "/!\ Oups, an error occured (this may not be fatal). /!\\\nPlease report this bug (by copying the present error message and explaining what you were doing) on the page http://dev.louiz.org/project/poezio/bugs/add\n\n%s\n\nIf Poezio does not respond anymore, kill it with Ctrl+\\, and sorry about that :(" % ''.join(traceback.format_exception(typ, value, trace)))
            self.add_tab(tb_tab, focus=True)
        except Exception:       # If an exception is raised in this code,
                                # this is fatal, so we exit cleanly and display the traceback
            self.reset_curses()
            raise

    def grow_information_win(self):
        """
        """
        if self.information_win_size == 14:
            return
        self.information_win_size += 1
        for tab in self.tabs:
            tab.on_info_win_size_changed()
        self.refresh_window()

    def shrink_information_win(self):
        """
        """
        if self.information_win_size == 0:
            return
        self.information_win_size -= 1
        for tab in self.tabs:
            tab.on_info_win_size_changed()
        self.refresh_window()

    def on_data_form(self, message):
        """
        When a data form is received
        """
        self.information('%s' % messsage)

    def on_chatstate_active(self, message):
        if message['type'] == 'chat': # normal conversation
            self.on_chatstate_normal_conversation(message, "active")

    def on_chatstate_inactive(self, message):
        if message['type'] == 'chat': # normal conversation
            self.on_chatstate_normal_conversation(message, "inactive")

    def on_chatstate_composing(self, message):
        if message['type'] == 'chat':
            self.on_chatstate_normal_conversation(message, "composing")

    def on_chatstate_paused(self, message):
        if message['type'] == 'chat':
            self.on_chatstate_normal_conversation(message, "paused")

    def on_chatstate_gone(self, message):
        if message['type'] == 'chat':
            self.on_chatstate_normal_conversation(message, "gone")

    def on_chatstate_normal_conversation(self, message,state):
        tab = self.get_tab_of_conversation_with_jid(message['from'], False)
        if not tab:
            return
        tab.chatstate = state

    def open_new_form(self, form, on_cancel, on_send, **kwargs):
        """
        Open a new tab containing the form
        The callback are called with the completed form as parameter in
        addition with kwargs
        """
        form_tab = DataFormsTab(self, form, on_cancel, on_send, kwargs)
        self.add_tab(form_tab, True)

    def on_got_offline(self, presence):
        jid = presence['from']
        contact = roster.get_contact_by_jid(jid.bare)
        if not contact:
            return
        resource = contact.get_resource_by_fulljid(jid.full)
        assert resource
        self.information('%s is offline' % (resource.get_jid()), "Roster")
        # If a resource got offline, display the message in the conversation with this
        # precise resource.
        self.add_information_message_to_conversation_tab(jid.full, '%s is offline' % (resource.get_jid().full))
        contact.remove_resource(resource)
        # Display the message in the conversation with the bare JID only if that was
        # the only resource online (i.e. now the contact is completely disconnected)
        if not contact.get_highest_priority_resource(): # No resource left: that was the last one
            self.add_information_message_to_conversation_tab(jid.bare, '%s is offline' % (jid.bare))
        self.refresh_window()

    def on_got_online(self, presence):
        jid = presence['from']
        contact = roster.get_contact_by_jid(jid.bare)
        if not contact:
            # Todo, handle presence comming from contacts not in roster
            return
        resource = contact.get_resource_by_fulljid(jid.full)
        assert not resource
        resource = Resource(jid.full)
        status = presence['type']
        status_message = presence['status']
        priority = presence.getPriority() or 0
        resource.set_status(status_message)
        resource.set_presence(status)
        resource.set_priority(priority)
        self.information("%s is online (%s)" % (resource.get_jid().full, status), "Roster")
        self.add_information_message_to_conversation_tab(jid.full, '%s is online' % (jid.full))
        if not contact.get_highest_priority_resource():
            # No connected resource yet: the user's just connecting
            self.add_information_message_to_conversation_tab(jid.bare, '%s is online' % (jid.bare))
        contact.add_resource(resource)

    def add_information_message_to_conversation_tab(self, jid, msg):
        """
        Search for a ConversationTab with the given jid (full or bare), if yes, add
        the given message to it
        """
        tab = self.get_tab_by_name(jid, tabs.ConversationTab)
        if tab:
            self.add_message_to_text_buffer(tab.get_room(), msg)

    def on_failed_connection(self):
        """
        We cannot contact the remote server
        """
        self.information(_("Connection to remote server failed"))

    def on_disconnected(self, event):
        """
        When we are disconnected from remote server
        """
        for tab in self.tabs:
            if isinstance(tab, tabs.MucTab):
                tab.get_room().disconnect()
        self.information(_("Disconnected from server."))

    def on_failed_auth(self, event):
        """
        Authentication failed
        """
        self.information(_("Authentication failed."))

    def on_connected(self, event):
        """
        Remote host responded, but we are not yet authenticated
        """
        self.information(_("Connected to server."))

    def on_session_start(self, event):
        """
        Called when we are connected and authenticated
        """
        self.information(_("Authentication success."))
        self.information(_("Your JID is %s") % self.xmpp.boundjid.full)
        if not self.xmpp.anon:
            # request the roster
            self.xmpp.getRoster()
            # send initial presence
            self.xmpp.makePresence().send()
        rooms = config.get('rooms', '')
        if rooms == '' or not isinstance(rooms, str):
            return
        rooms = rooms.split(':')
        for room in rooms:
            jid = JID(room)
            if jid.bare == '':
                return
            if jid.resource != '':
                nick = jid.resource
            else:
                default = os.environ.get('USER') if os.environ.get('USER') else 'poezio'
                nick = config.get('default_nick', '') or default
            tab = self.get_tab_by_name(jid.bare, tabs.MucTab)
            if not tab:
                self.open_new_room(jid.bare, nick, False)
            muc.join_groupchat(self.xmpp, jid.bare, nick)
        # if not self.xmpp.anon:
        # Todo: SEND VCARD
        return
        if config.get('jid', '') == '': # Don't send the vcard if we're not anonymous
            self.vcard_sender.start()   # because the user ALREADY has one on the server

    def on_groupchat_presence(self, presence):
        """
        Triggered whenever a presence stanza is received from a user in a multi-user chat room.
        Display the presence on the room window and update the
        presence information of the concerned user
        """
        from_nick = presence['from'].resource
        from_room = presence['from'].bare
        room = self.get_room_by_name(from_room)
        code = presence.find('{jabber:client}status')
        status_codes = set([s.attrib['code'] for s in presence.findall('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}status')])
        # Check if it's not an error presence.
        if presence['type'] == 'error':
            return self.room_error(presence, from_room)
        if not room:
            return
        msg = None
        affiliation = presence['muc']['affiliation']
        show = presence['show']
        status = presence['status']
        role = presence['muc']['role']
        jid = presence['muc']['jid']
        typ = presence['type']
        if not room.joined:     # user in the room BEFORE us.
            # ignore redondant presence message, see bug #1509
            if from_nick not in [user.nick for user in room.users]:
                new_user = User(from_nick, affiliation, show, status, role, jid)
                room.users.append(new_user)
                if from_nick == room.own_nick:
                    room.joined = True
                    new_user.color = theme.COLOR_OWN_NICK
                    self.add_message_to_text_buffer(room, _("Your nickname is %s") % (from_nick))
                    if '170' in status_codes:
                        self.add_message_to_text_buffer(room, 'Warning: this room is publicly logged')
        else:
            change_nick = '303' in status_codes
            kick = '307' in status_codes and typ == 'unavailable'
            ban = '301' in status_codes and typ == 'unavailable'
            user = room.get_user_by_name(from_nick)
            # New user
            if not user:
                self.on_user_join(room, from_nick, affiliation, show, status, role, jid)
            # nick change
            elif change_nick:
                self.on_user_nick_change(room, presence, user, from_nick, from_room)
            elif ban:
                self.on_user_banned(room, presence, user, from_nick)
            # kick
            elif kick:
                self.on_user_kicked(room, presence, user, from_nick)
            # user quit
            elif typ == 'unavailable':
                self.on_user_leave_groupchat(room, user, jid, status, from_nick, from_room)
            # status change
            else:
                self.on_user_change_status(room, user, from_nick, from_room, affiliation, role, show, status)
        self.refresh_window()
        self.doupdate()

    def on_user_join(self, room, from_nick, affiliation, show, status, role, jid):
        """
        When a new user joins a groupchat
        """
        room.users.append(User(from_nick, affiliation,
                               show, status, role, jid))
        hide_exit_join = config.get('hide_exit_join', -1)
        if hide_exit_join != 0:
            if not jid.full:
                self.add_message_to_text_buffer(room, _('%(spec)s "[%(nick)s]" joined the room') % {'nick':from_nick.replace('"', '\\"'), 'spec':theme.CHAR_JOIN.replace('"', '\\"')}, colorized=True)
            else:
                self.add_message_to_text_buffer(room, _('%(spec)s "[%(nick)s]" "(%(jid)s)" joined the room') % {'spec':theme.CHAR_JOIN.replace('"', '\\"'), 'nick':from_nick.replace('"', '\\"'), 'jid':jid.full}, colorized=True)

    def on_user_nick_change(self, room, presence, user, from_nick, from_room):
        new_nick = presence.find('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}item').attrib['nick']
        if user.nick == room.own_nick:
            room.own_nick = new_nick
            # also change our nick in all private discussion of this room
            for _tab in self.tabs:
                if isinstance(_tab, tabs.PrivateTab) and JID(_tab.get_name()).bare == room.name:
                    _tab.get_room().own_nick = new_nick
        user.change_nick(new_nick)
        self.add_message_to_text_buffer(room, _('"[%(old)s]" is now known as "[%(new)s]"') % {'old':from_nick.replace('"', '\\"'), 'new':new_nick.replace('"', '\\"')}, colorized=True)
        # rename the private tabs if needed
        private_room = self.get_room_by_name('%s/%s' % (from_room, from_nick))
        if private_room:
            self.add_message_to_text_buffer(private_room, _('"[%(old_nick)s]" is now known as "[%(new_nick)s]"') % {'old_nick':from_nick.replace('"', '\\"'), 'new_nick':new_nick.replace('"', '\\"')}, colorized=True)
            new_jid = JID(private_room.name).bare+'/'+new_nick
            private_room.name = new_jid

    def on_user_banned(self, room, presence, user, from_nick):
        """
        When someone is banned from a muc
        """
        room.users.remove(user)
        by = presence.find('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}item/{http://jabber.org/protocol/muc#user}actor')
        reason = presence.find('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}item/{http://jabber.org/protocol/muc#user}reason')
        by = by.attrib['jid'] if by is not None else None
        if from_nick == room.own_nick: # we are banned
            room.disconnect()
            if by:
                kick_msg = _('%(spec)s [You] have been banned by "[%(by)s]"') % {'spec': theme.CHAR_KICK.replace('"', '\\"'), 'by':by}
            else:
                kick_msg = _('%(spec)s [You] have been banned.') % {'spec':theme.CHAR_KICK.replace('"', '\\"')}
        else:
            if by:
                kick_msg = _('%(spec)s "[%(nick)s]" has been banned by "[%(by)s]"') % {'spec':theme.CHAR_KICK.replace('"', '\\"'), 'nick':from_nick.replace('"', '\\"'), 'by':by.replace('"', '\\"')}
            else:
                kick_msg = _('%(spec)s "[%(nick)s]" has been banned') % {'spec':theme.CHAR_KICK, 'nick':from_nick.replace('"', '\\"')}
        if reason is not None and reason.text:
            kick_msg += _(' Reason: %(reason)s') % {'reason': reason.text}
        self.add_message_to_text_buffer(room, kick_msg, colorized=True)

    def on_user_kicked(self, room, presence, user, from_nick):
        """
        When someone is kicked from a muc
        """
        room.users.remove(user)
        by = presence.find('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}item/{http://jabber.org/protocol/muc#user}actor')
        reason = presence.find('{http://jabber.org/protocol/muc#user}x/{http://jabber.org/protocol/muc#user}item/{http://jabber.org/protocol/muc#user}reason')
        by = by.attrib['jid'] if by is not None else None
        if from_nick == room.own_nick: # we are kicked
            room.disconnect()
            if by:
                kick_msg = _('%(spec)s [You] have been kicked by "[%(by)s]"') % {'spec': theme.CHAR_KICK.replace('"', '\\"'), 'by':by}
            else:
                kick_msg = _('%(spec)s [You] have been kicked.') % {'spec':theme.CHAR_KICK.replace('"', '\\"')}
            # try to auto-rejoin
            if config.get('autorejoin', 'false') == 'true':
                muc.join_groupchat(self.xmpp, room.name, room.own_nick)
        else:
            if by:
                kick_msg = _('%(spec)s "[%(nick)s]" has been kicked by "[%(by)s]"') % {'spec':theme.CHAR_KICK.replace('"', '\\"'), 'nick':from_nick.replace('"', '\\"'), 'by':by.replace('"', '\\"')}
            else:
                kick_msg = _('%(spec)s "[%(nick)s]" has been kicked') % {'spec':theme.CHAR_KICK, 'nick':from_nick.replace('"', '\\"')}
        if reason is not None and reason.text:
            kick_msg += _(' Reason: %(reason)s') % {'reason': reason.text}
        self.add_message_to_text_buffer(room, kick_msg, colorized=True)

    def on_user_leave_groupchat(self, room, user, jid, status, from_nick, from_room):
        """
        When an user leaves a groupchat
        """
        room.users.remove(user)
        if room.own_nick == user.nick:
            # We are now out of the room. Happens with some buggy (? not sure) servers
            room.disconnect()
        hide_exit_join = config.get('hide_exit_join', -1) if config.get('hide_exit_join', -1) >= -1 else -1
        if hide_exit_join == -1 or user.has_talked_since(hide_exit_join):
            if not jid.full:
                leave_msg = _('%(spec)s "[%(nick)s]" has left the room') % {'nick':from_nick.replace('"', '\\"'), 'spec':theme.CHAR_QUIT.replace('"', '\\"')}
            else:
                leave_msg = _('%(spec)s "[%(nick)s]" "(%(jid)s)" has left the room') % {'spec':theme.CHAR_QUIT.replace('"', '\\"'), 'nick':from_nick.replace('"', '\\"'), 'jid':jid.full.replace('"', '\\"')}
            if status:
                leave_msg += ' (%s)' % status
            self.add_message_to_text_buffer(room, leave_msg, colorized=True)
        private_room = self.get_room_by_name('%s/%s' % (from_room, from_nick))
        if private_room:
            if not status:
                self.add_message_to_text_buffer(private_room, _('%(spec)s "[%(nick)s]" has left the room') % {'nick':from_nick.replace('"', '\\"'), 'spec':theme.CHAR_QUIT.replace('"', '\\"')}, colorized=True)
            else:
                self.add_message_to_text_buffer(private_room, _('%(spec)s "[%(nick)s]" has left the room "(%(status)s)"') % {'nick':from_nick.replace('"', '\\"'), 'spec':theme.CHAR_QUIT, 'status': status.replace('"', '\\"')}, colorized=True)

    def on_user_change_status(self, room, user, from_nick, from_room, affiliation, role, show, status):
        """
        When an user changes her status
        """
        # build the message
        display_message = False # flag to know if something significant enough
        # to be displayed has changed
        msg = _('"%s" changed: ')% from_nick.replace('"', '\\"')
        if affiliation != user.affiliation:
            msg += _('affiliation: %s, ') % affiliation
            display_message = True
        if role != user.role:
            msg += _('role: %s, ') % role
            display_message = True
        if show != user.show and show in list(SHOW_NAME.keys()):
            msg += _('show: %s, ') % SHOW_NAME[show]
            display_message = True
        if status and status != user.status:
            msg += _('status: %s, ') % status
            display_message = True
        if not display_message:
            return
        msg = msg[:-2] # remove the last ", "
        hide_status_change = config.get('hide_status_change', -1) if config.get('hide_status_change', -1) >= -1 else -1
        if (hide_status_change == -1 or \
                user.has_talked_since(hide_status_change) or\
                user.nick == room.own_nick)\
                and\
                (affiliation != user.affiliation or\
                    role != user.role or\
                    show != user.show or\
                    status != user.status):
            # display the message in the room
            self.add_message_to_text_buffer(room, msg, colorized=True)
        private_room = self.get_room_by_name('%s/%s' % (from_room, from_nick))
        if private_room: # display the message in private
            self.add_message_to_text_buffer(private_room, msg, colorized=True)
        # finally, effectively change the user status
        user.update(affiliation, show, status, role)

    def on_message(self, message):
        """
        When receiving private message from a muc OR a normal message
        (from one of our contacts)
        """
        if message['type'] == 'groupchat':
            return
        # Differentiate both type of messages, and call the appropriate handler.
        jid_from = message['from']
        for tab in self.tabs:
            if tab.get_name() == jid_from.bare and isinstance(tab, tabs.MucTab):
                if message['type'] == 'error':
                    return self.room_error(message, tab.get_room().name)
                else:
                    return self.on_groupchat_private_message(message)
        return self.on_normal_message(message)

    def on_groupchat_private_message(self, message):
        """
        We received a Private Message (from someone in a Muc)
        """
        jid = message['from']
        nick_from = jid.resource
        room_from = jid.bare
        room = self.get_room_by_name(jid.full) # get the tab with the private conversation
        if not room: # It's the first message we receive: create the tab
            room = self.open_private_window(room_from, nick_from, False)
            if not room:
                return
        body = message['body']
        room.add_message(body, time=None, nickname=nick_from,
                         colorized=False,
                         forced_user=self.get_room_by_name(room_from).get_user_by_name(nick_from))
        logger.log_message(jid.full.replace('/', '\\'), nick_from, body)
        self.refresh_window()
        self.doupdate()

    def focus_tab_named(self, tab_name):
        for tab in self.tabs:
            if tab.get_name() == tab_name:
                self.command_win('%s' % (tab.nb,))

    def get_tab_of_conversation_with_jid(self, jid, create=True):
        """
        From a JID, get the tab containing the conversation with it.
        If none already exist, and create is "True", we create it
        and return it. Otherwise, we return None
        """
        # We first check if we have a conversation opened with this precise resource
        conversation = self.get_tab_by_name(jid.full, tabs.ConversationTab)
        if not conversation:
            # If not, we search for a conversation with the bare jid
            conversation = self.get_tab_by_name(jid.bare, tabs.ConversationTab)
            if not conversation:
                if create:
                    # We create the conversation with the bare Jid if nothing was found
                    conversation = self.open_conversation_window(jid.bare, False)
                else:
                    conversation = None
        return conversation

    def on_normal_message(self, message):
        """
        When receiving "normal" messages (from someone in our roster)
        """
        jid = message['from']
        body = message['body']
        if not body:
            return
        conversation = self.get_tab_of_conversation_with_jid(jid, create=True)
        if roster.get_contact_by_jid(jid.bare):
            remote_nick = roster.get_contact_by_jid(jid.bare).get_name() or jid.user
        else:
            remote_nick = jid.user
        conversation.get_room().add_message(body, None, remote_nick, False, theme.COLOR_REMOTE_USER)
        if conversation.remote_wants_chatstates is None:
            if message['chat_state']:
                conversation.remote_wants_chatstates = True
            else:
                conversation.remote_wants_chatstates = False
        logger.log_message(jid.bare, remote_nick, body)
        if self.current_tab() is not conversation:
            conversation.set_color_state(theme.COLOR_TAB_PRIVATE)
        self.refresh_window()

    def on_presence(self, presence):
        jid = presence['from']
        contact = roster.get_contact_by_jid(jid.bare)
        if not contact:
            return
        resource = contact.get_resource_by_fulljid(jid.full)
        if not resource:
            return
        status = presence['type']
        status_message = presence['status']
        priority = presence.getPriority() or 0
        resource.set_presence(status)
        resource.set_priority(priority)
        resource.set_status(status_message)
        self.refresh_window()

    def on_roster_update(self, iq):
        """
        A subscription changed, or we received a roster item
        after a roster request, etc
        """
        for item in iq.findall('{jabber:iq:roster}query/{jabber:iq:roster}item'):
            jid = item.attrib['jid']
            contact = roster.get_contact_by_jid(jid)
            if not contact:
                contact = Contact(jid)
                roster.add_contact(contact, jid)
            if 'ask' in item.attrib:
                contact.set_ask(item.attrib['ask'])
            else:
                contact.set_ask(None)
            if 'name' in item.attrib:
                contact.set_name(item.attrib['name'])
            else:
                contact.set_name(None)
            if item.attrib['subscription']:
                contact.set_subscription(item.attrib['subscription'])
            groups = item.findall('{jabber:iq:roster}group')
            roster.edit_groups_of_contact(contact, [group.text for group in groups])
            if item.attrib['subscription'] == 'remove':
                roster.remove_contact(contact.get_bare_jid())
        if isinstance(self.current_tab(), tabs.RosterInfoTab):
            self.refresh_window()

    def on_changed_subscription(self, presence):
        """
        Triggered whenever a presence stanza with a type of subscribe, subscribed, unsubscribe, or unsubscribed is received.
        """
        if presence['type'] == 'subscribe':
            jid = presence['from'].bare
            contact = roster.get_contact_by_jid(jid)
            if not contact:
                contact = Contact(jid)
                roster.add_contact(contact, jid)
            roster.edit_groups_of_contact(contact, [])
            contact.set_ask('asked')
            self.tabs[0].set_color_state(theme.COLOR_TAB_HIGHLIGHT)
            self.information('%s wants to subscribe to your presence'%jid)
        if isinstance(self.current_tab(), tabs.RosterInfoTab):
            self.refresh_window()

    def full_screen_redraw(self):
        """
        Completely erase and redraw the screen
        """
        self.stdscr.clear()
        self.call_for_resize()

    def call_for_resize(self):
        """
        Called when we want to resize the screen
        """
        with resize_lock:
            for tab in self.tabs:
                tab.need_resize = True
            self.refresh_window()

    def main_loop(self):
        """
        main loop waiting for the user to press a key
        """
        curses.ungetch(0)    # FIXME
        while self.running:
            char = read_char(self.stdscr)
            # search for keyboard shortcut
            if char in list(self.key_func.keys()):
                self.key_func[char]()
            else:
                self.do_command(char)
            self.doupdate()

    def current_tab(self):
        """
        returns the current room, the one we are viewing
        """
        return self.tabs[0]

    def get_conversation_by_jid(self, jid):
        """
        Return the room of the ConversationTab with the given jid
        """
        for tab in self.tabs:
            if isinstance(tab, tabs.ConversationTab):
                if tab.get_name() == jid:
                    return tab.get_room()
        return None

    def get_tab_by_name(self, name, typ=None):
        """
        Get the tab with the given name.
        If typ is provided, return a tab of this type only
        """
        for tab in self.tabs:
            if tab.get_name() == name:
                if (typ and isinstance(tab, typ)) or\
                        not typ:
                    return tab
        return None

    def get_room_by_name(self, name):
        """
        returns the room that has this name
        """
        for tab in self.tabs:
            if (isinstance(tab, tabs.MucTab) or
                isinstance(tab, tabs.PrivateTab)) and tab.get_name() == name:
                return tab.get_room()
        return None

    def init_curses(self, stdscr):
        """
        ncurses initialization
        """
        curses.curs_set(1)
        curses.noecho()
        curses.nonl()
        theme.init_colors()
        stdscr.keypad(True)

    def reset_curses(self):
        """
        Reset terminal capabilities to what they were before ncurses
        init
        """
        curses.echo()
        curses.nocbreak()
        curses.curs_set(1)
        curses.endwin()

    def refresh_window(self):
        """
        Refresh everything
        """
        self.current_tab().set_color_state(theme.COLOR_TAB_CURRENT)
        self.current_tab().refresh(self.tabs, self.information_buffer, roster)
        self.doupdate()

    def add_tab(self, new_tab, focus=False):
        """
        Appends the new_tab in the tab list and
        focus it if focus==True
        """
        if self.current_tab().nb == 0:
            self.tabs.append(new_tab)
        else:
            for ta in self.tabs:
                if ta.nb == 0:
                    self.tabs.insert(self.tabs.index(ta), new_tab)
                    break
        if focus:
            self.command_win("%s" % new_tab.nb)

    def open_new_room(self, room, nick, focus=True):
        """
        Open a new tab.MucTab containing a muc Room, using the specified nick
        """
        r = Room(room, nick)
        new_tab = tabs.MucTab(self, r)
        self.add_tab(new_tab, focus)
        self.refresh_window()

    def go_to_roster(self):
        self.command_win('0')

    def go_to_previous_tab(self):
        self.command_win('%s' % (self.previous_tab_nb,))

    def go_to_important_room(self):
        """
        Go to the next room with activity, in this order:
        - A personal conversation with a new message
        - A Muc with an highlight
        - A Muc with any new message
        """
        for tab in self.tabs:
            if tab.get_color_state() == theme.COLOR_TAB_PRIVATE:
                self.command_win('%s' % tab.nb)
                return
        for tab in self.tabs:
            if tab.get_color_state() == theme.COLOR_TAB_HIGHLIGHT:
                self.command_win('%s' % tab.nb)
                return
        for tab in self.tabs:
            if tab.get_color_state() == theme.COLOR_TAB_NEW_MESSAGE:
                self.command_win('%s' % tab.nb)
                return
        for tab in self.tabs:
            if isinstance(tab, tabs.ChatTab) and not tab.input.is_empty():
                self.command_win('%s' % tab.nb)
                return

    def rotate_rooms_right(self, args=None):
        """
        rotate the rooms list to the right
        """
        self.current_tab().on_lose_focus()
        self.tabs.append(self.tabs.pop(0))
        self.current_tab().on_gain_focus()
        self.refresh_window()

    def rotate_rooms_left(self, args=None):
        """
        rotate the rooms list to the right
        """
        self.current_tab().on_lose_focus()
        self.tabs.insert(0, self.tabs.pop())
        self.current_tab().on_gain_focus()
        self.refresh_window()

    def scroll_page_down(self, args=None):
        self.current_tab().on_scroll_down()
        self.refresh_window()

    def scroll_page_up(self, args=None):
        self.current_tab().on_scroll_up()
        self.refresh_window()

    def room_error(self, error, room_name):
        """
        Display the error on the room window
        """
        room = self.get_room_by_name(room_name)
        msg = error['error']['type']
        condition = error['error']['condition']
        code = error['error']['code']
        body = error['error']['text']
        if not body:
            if code in list(ERROR_AND_STATUS_CODES.keys()):
                body = ERROR_AND_STATUS_CODES[code]
            else:
                body = condition or _('Unknown error')
        if code:
            msg = _('Error: %(code)s - %(msg)s: %(body)s') % {'msg':msg, 'body':body, 'code':code}
            self.add_message_to_text_buffer(room, msg)
        else:
            msg = _('Error: %(msg)s: %(body)s') % {'msg':msg, 'body':body}
            self.add_message_to_text_buffer(room, msg)
        if code == '401':
            msg = _('To provide a password in order to join the room, type "/join / password" (replace "password" by the real password)')
            self.add_message_to_text_buffer(room, msg)
        if code == '409':
            if config.get('alternative_nickname', '') != '':
                self.command_join('%s/%s'% (room.name, room.own_nick+config.get('alternative_nickname', '')))
            else:
                self.add_message_to_text_buffer(room, _('You can join the room with an other nick, by typing "/join /other_nick"'))
        self.refresh_window()

    def open_conversation_window(self, jid, focus=True):
        """
        open a new conversation tab and focus it if needed
        """
        new_tab = tabs.ConversationTab(self, jid)
        # insert it in the rooms
        self.add_tab(new_tab, focus)
        self.refresh_window()
        return new_tab

    def open_private_window(self, room_name, user_nick, focus=True):
        complete_jid = room_name+'/'+user_nick
        for tab in self.tabs: # if the room exists, focus it and return
            if isinstance(tab, tabs.PrivateTab):
                if tab.get_name() == complete_jid:
                    self.command_win('%s' % tab.nb)
                    return tab.get_room()
        # create the new tab
        room = self.get_room_by_name(room_name)
        if not room:
            return None
        own_nick = room.own_nick
        r = Room(complete_jid, own_nick) # PrivateRoom here
        new_tab = tabs.PrivateTab(self, r)
        # insert it in the tabs
        self.add_tab(new_tab, focus)
        # self.window.new_room(r)
        self.refresh_window()
        return r

    def on_groupchat_subject(self, message):
        """
        triggered when the topic is changed
        """
        nick_from = message['mucnick']
        room_from = message.getMucroom()
        room = self.get_room_by_name(room_from)
        subject = message['subject']
        if not subject or not room:
            return
        if nick_from:
            self.add_message_to_text_buffer(room, _("%(nick)s set the subject to: %(subject)s") % {'nick':nick_from, 'subject':subject}, time=None)
        else:
            self.add_message_to_text_buffer(room, _("The subject is: %(subject)s") % {'subject':subject}, time=None)
        room.topic = subject.replace('\n', '|')
        self.refresh_window()

    def on_groupchat_message(self, message):
        """
        Triggered whenever a message is received from a multi-user chat room.
        """
        if message['subject']:
            return
        delay_tag = message.find('{urn:xmpp:delay}delay')
        if delay_tag is not None:
            delayed = True
            date = common.datetime_tuple(delay_tag.attrib['stamp'])
        else:
            # We support the OLD and deprecated XEP: http://xmpp.org/extensions/xep-0091.html
            # But it sucks, please, Jabber servers, don't do this :(
            delay_tag = message.find('{jabber:x:delay}x')
            if delay_tag is not None:
                delayed = True
                date = common.datetime_tuple(delay_tag.attrib['stamp'])
            else:
                delayed = False
                date = None
        nick_from = message['mucnick']
        room_from = message.getMucroom()
        if message['type'] == 'error': # Check if it's an error
            return self.room_error(message, from_room)
        if nick_from == room_from:
            nick_from = None
        room = self.get_room_by_name(room_from)
        tab = self.get_tab_by_name(room_from, tabs.MucTab)
        if tab and tab.get_room() and tab.get_room().get_user_by_name(nick_from) and\
                tab.get_room().get_user_by_name(nick_from) in tab.ignores:
            return
        if not room:
            self.information(_("message received for a non-existing room: %s") % (room_from))
            return
        body = message['body']
        if body:
            date = date if delayed == True else None
            self.add_message_to_text_buffer(room, body, date, nick_from)
            # TODO, only if we are focused on this MUC
            self.refresh_window()
            self.doupdate()

    def add_message_to_text_buffer(self, room, txt, time=None, nickname=None, colorized=False):
        """
        Add the message to the room if possible, else, add it to the Info window
        (in the Info tab of the info window in the RosterTab)
        """
        if not room:
            self.information('Error, trying to add a message in no room: %s' % txt)
        else:
            room.add_message(txt, time, nickname, colorized)
        self.refresh_window()

    def command_help(self, arg):
        """
        /help <command_name>
        """
        args = arg.split()
        if len(args) == 0:
            msg = _('Available commands are: ')
            for command in list(self.commands.keys()):
                msg += "%s " % command
            for command in list(self.current_tab().commands.keys()):
                msg += "%s " % command
            msg += _("\nType /help <command_name> to know what each command does")
        if len(args) >= 1:
            if args[0] in list(self.commands.keys()):
                msg = self.commands[args[0]][1]
            elif args[0] in list(self.current_tab().commands.keys()):
                msg = self.current_tab().commands[args[0]][1]
            else:
                msg = _('Unknown command: %s') % args[0]
        self.information(msg)

    def completion_help(self, the_input):
        commands = list(self.commands.keys()) + list(self.current_tab().commands.keys())
        return the_input.auto_completion(commands, ' ')

    def command_status(self, arg):
        args = arg.split()
        if len(args) < 1:
            return
        if not args[0] in possible_show.keys():
            self.command_help('show')
            return
        show = possible_show[args[0]]
        if len(args) > 1:
            msg = ' '.join(args[1:])
        else:
            msg = None
        pres = self.xmpp.make_presence()
        if msg:
            pres['status'] = msg
        pres['type'] = show
        pres.send()
        self.command_show(arg)

    def completion_status(self, the_input):
        return the_input.auto_completion([status for status in list(possible_show.keys())], ' ')

    def command_message(self, arg):
        """
        /message <jid> [message]
        """
        args = arg.split()
        if len(args) < 1:
            self.command_help('message')
            return
        jid = args[0]
        tab = self.open_conversation_window(jid, focus=True)
        if len(args) > 1:
            tab.command_say(arg.strip()[len(jid):])

    def command_list(self, arg):
        """
        /list <server>
        Opens a MucListTab containing the list of the room in the specified server
        """
        args = arg.split()
        if len(args) > 1:
            self.command_help('list')
            return
        elif len(args) == 0:
            if not isinstance(self.current_tab(), tabs.MucTab):
                return self.information('Warning: Please provide a server')
            server = JID(self.current_tab().get_name()).server
        else:
            server = arg.strip()
        list_tab = tabs.MucListTab(self, server)
        self.add_tab(list_tab, True)
        self.xmpp.plugin['xep_0030'].get_items(jid=server, block=False, callback=list_tab.on_muc_list_item_received)

    def command_theme(self, arg):
        """
        """
        theme.reload_theme()
        self.refresh_window()

    def command_win(self, arg):
        """
        /win <number>
        """
        args = arg.split()
        if len(args) != 1:
            self.command_help('win')
            return
        try:
            nb = int(args[0])
        except ValueError:
            nb = arg.strip()
        if self.current_tab().nb == nb:
            return
        self.previous_tab_nb = self.current_tab().nb
        self.current_tab().on_lose_focus()
        start = self.current_tab()
        self.tabs.append(self.tabs.pop(0))
        if isinstance(nb, int):
            while self.current_tab().nb != nb:
                self.tabs.append(self.tabs.pop(0))
                if self.current_tab() == start:
                    break
        else:
            while nb not in JID(self.current_tab().get_name()).user:
                self.tabs.append(self.tabs.pop(0))
                if self.current_tab() is start:
                    break
        self.current_tab().on_gain_focus()
        self.refresh_window()

    def completion_win(self, the_input):
        l = [JID(tab.get_name()).user for tab in self.tabs]
        l.remove('')
        return the_input.auto_completion(l, ' ')

    def completion_join(self, the_input):
        """
        Try to complete the server of the MUC's jid (for now only from the currently
        open ones)
        TODO: have a history of recently joined MUCs, and use that too
        """
        txt = the_input.get_text()
        if len(txt.split()) != 2:
            # we are not on the 1st argument of the command line
            return False
        jid = JID(txt.split()[1])
        if jid.server:
            if jid.resource or jid.full.endswith('/'):
                # we are writing the resource: complete the node
                if not the_input.last_completion:
                    response = self.xmpp.plugin['xep_0030'].get_items(jid=jid.server, block=True, timeout=1)
                    if response:
                        items = response['disco_items'].get_items()
                    else:
                        return True
                    items = ['%s/%s' % (tup[0], jid.resource) for tup in items]
                    for i in range(len(jid.server) + 2 + len(jid.resource)):
                        the_input.key_backspace()
                else:
                    items = []
                the_input.auto_completion(items, '')
            else:
                # we are writing the server: complete the server
                serv = jid.server
                serv_list = []
                for tab in self.tabs:
                    if isinstance(tab, tabs.MucTab):
                        serv_list.append('%s@%s'% (jid.user, JID(tab.get_name()).host))
                the_input.auto_completion(serv_list, '')
        return True

    def completion_list(self, the_input):
        txt = the_input.get_text()
        muc_serv_list = []
        for tab in self.tabs:   # TODO, also from an history
            if isinstance(tab, tabs.MucTab) and\
                    tab.get_name() not in muc_serv_list:
                muc_serv_list.append(JID(tab.get_name()).server)
        if muc_serv_list:
            return the_input.auto_completion(muc_serv_list, ' ')

    def command_join(self, arg, histo_length=None):
        """
        /join [room][/nick] [password]
        """
        args = arg.split()
        password = None
        if len(args) == 0:
            t = self.current_tab()
            if not isinstance(t, tabs.MucTab) and not isinstance(t, tabs.PrivateTab):
                return
            room = t.get_name()
            nick = t.get_room().own_nick
        else:
            info = JID(args[0])
            if info.resource == '':
                default = os.environ.get('USER') if os.environ.get('USER') else 'poezio'
                nick = config.get('default_nick', '')
                if nick == '':
                    nick = default
            else:
                nick = info.resource
            if info.bare == '':   # happens with /join /nickname, which is OK
                t = self.current_tab()
                if not isinstance(t, tabs.MucTab):
                    return
                room = t.get_name()
                if nick == '':
                    nick = t.get_room().own_nick
            else:
                room = info.bare
            if room.find('@') == -1: # no server is provided, like "/join hello"
                # use the server of the current room if available
                # check if the current room's name has a server
                if isinstance(self.current_tab(), tabs.MucTab) and\
                        self.current_tab().get_name().find('@') != -1:
                    room += '@%s' % JID(self.current_tab().get_name()).domain
                else:           # no server could be found, print a message and return
                    self.information(_("You didn't specify a server for the room you want to join"), 'Error')
                    return
        r = self.get_room_by_name(room)
        if len(args) == 2:       # a password is provided
            password = args[1]
        if r and r.joined:       # if we are already in the room
            self.focus_tab_named(r.name)
            return
        if room.startswith('@'):
            room = room[1:]
        room = room.lower()
        if r and not r.joined:
            muc.join_groupchat(self.xmpp, room, nick, password, histo_length)
        if not r:   # if the room window exists, we don't recreate it.
            self.open_new_room(room, nick)
            muc.join_groupchat(self.xmpp, room, nick, password, histo_length)
        else:
            r.own_nick = nick
            r.users = []

    def command_bookmark(self, arg):
        """
        /bookmark [room][/nick]
        """
        args = arg.split()
        nick = None
        if len(args) == 0 and not isinstance(self.current_tab(), tabs.MucTab):
            return
        if len(args) == 0:
            room = self.current_tab().get_room()
            roomname = self.current_tab().get_name()
            if room.joined:
                nick = room.own_nick
        else:
            info = JID(args[0])
            if info.resource != '':
                nick = info.resource
            roomname = info.bare
            if roomname == '':
                roomname = self.current_tab().get_name()
        if nick:
            res = roomname+'/'+nick
        else:
            res = roomname
        bookmarked = config.get('rooms', '')
        # check if the room is already bookmarked.
        # if yes, replace it (i.e., update the associated nick)
        bookmarked = bookmarked.split(':')
        for room in bookmarked:
            if JID(room).bare == roomname:
                bookmarked.remove(room)
                break
        bookmarked = ':'.join(bookmarked)
        if bookmarked:
            bookmarks = bookmarked+':'+res
        else:
            bookmarks = res
        config.set_and_save('rooms', bookmarks)
        self.information(_('Your bookmarks are now: %s') % bookmarks)

    def command_set(self, arg):
        """
        /set <option> [value]
        """
        args = arg.split()
        if len(args) != 2 and len(args) != 1:
            self.command_help('set')
            return
        option = args[0]
        if len(args) == 2:
            value = args[1]
        else:
            value = ''
        config.set_and_save(option, value)
        msg = "%s=%s" % (option, value)
        self.information(msg)

    def command_show(self, arg):
        """
        /show <status> [msg]
        """
        args = arg.split()
        if len(args) < 1:
            return
        if not args[0] in list(possible_show.keys()):
            self.command_help('show')
            return
        show = possible_show[args[0]]
        if len(args) > 1:
            msg = ' '.join(args[1:])
        else:
            msg = None
        for tab in self.tabs:
            if isinstance(tab, tabs.MucTab) and tab.get_room().joined:
                muc.change_show(self.xmpp, tab.get_room().name, tab.get_room().own_nick, show, msg)

    def completion_show(self, the_input):
        return the_input.auto_completion([status for status in list(possible_show.keys())], ' ')

    def command_away(self, arg):
        """
        /away [msg]
        """
        self.command_show("away "+arg)

    def command_busy(self, arg):
        """
        /busy [msg]
        """
        self.command_show("busy "+arg)

    def command_avail(self, arg):
        """
        /avail [msg]
        """
        self.command_show("available "+arg)

    def close_tab(self, tab=None):
        """
        Close the given tab. If None, close the current one
        """
        tab = tab or self.current_tab()
        if isinstance(tab, tabs.RosterInfoTab) or\
                isinstance(tab, tabs.InfoTab):
            return              # The tab 0 should NEVER be closed
        tab.on_close()
        self.tabs.remove(tab)
        self.rotate_rooms_left()
        del tab.key_func        # Remove self references
        del tab.commands        # and make the object collectable
        del tab

    def move_separator(self):
        """
        Move the new-messages separator at the bottom on the current
        text.
        """
        window = self.current_tab().get_text_window()
        if not window:
            return
        window.remove_line_separator()
        window.add_line_separator()
        self.refresh_window()

    def information(self, msg, typ=''):
        """
        Displays an informational message in the "Info" room window
        """
        self.information_buffer.add_message(msg, nickname=typ)
        # TODO: refresh only the correct window in the current tab
        self.refresh_window()

    def command_quit(self, arg):
        """
        /quit
        """
        if len(arg.strip()) != 0:
            msg = arg
        else:
            msg = None
        for tab in self.tabs:
            if isinstance(tab, tabs.MucTab):
                muc.leave_groupchat(self.xmpp, tab.get_room().name, tab.get_room().own_nick, msg)
        self.save_config()
        self.xmpp.disconnect()
        self.running = False
        self.reset_curses()
        sys.exit()

    def save_config(self):
        """
        Save config in the file just before exit
        """
        roster.save_to_config_file()
        config.set_and_save('info_win_height', self.information_win_size, 'var')

    def do_command(self, key):
        if not key:
            return
        res = self.current_tab().on_input(key)
        if res:
            self.refresh_window()

    def on_roster_enter_key(self, roster_row):
        """
        when enter is pressed on the roster window
        """
        if isinstance(roster_row, Contact):
            if not self.get_conversation_by_jid(roster_row.get_bare_jid()):
                self.open_conversation_window(roster_row.get_bare_jid())
            else:
                self.focus_tab_named(roster_row.get_bare_jid())
        if isinstance(roster_row, Resource):
            if not self.get_conversation_by_jid(roster_row.get_jid().full):
                self.open_conversation_window(roster_row.get_jid().full)
            else:
                self.focus_tab_named(roster_row.get_jid().full)
        self.refresh_window()

    def execute(self,line):
        """
        Execute the /command or just send the line on the current room
        """
        if line == "":
            return
        if line.startswith('/'):
            command = line.strip()[:].split()[0][1:]
            arg = line[2+len(command):] # jump the '/' and the ' '
            # example. on "/link 0 open", command = "link" and arg = "0 open"
            if command in list(self.commands.keys()):
                func = self.commands[command][0]
                func(arg)
                return
            else:
                self.information(_("unknown command (%s)") % (command), _('Error'))

    def doupdate(self):
        if not self.running:
            return
        self.current_tab().just_before_refresh()
        curses.doupdate()

# global core object
core = Core(connection)
