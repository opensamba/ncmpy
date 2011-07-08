#!/usr/bin/python2
# -*- coding: utf-8 -*-
#
# MPY - a [Python + Curses]-based MPD client.
# 
# Copyright (C) 2011 Cyker Way
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

'''MPY is a MPD client written in Python using Curses.

    udata -> round_one -> round_two -> uwin

            main    mod_1   mod_2 ... mod_n

udata       *   ->  *   ->  *   ->  ->  *
                                        |
            <-  <-  <-  <-  <-  <-  <-   
            |
round_one   *   ->  *   ->  *   ->  ->  *
                                        |
            <-  <-  <-  <-  <-  <-  <-   
            |
round_two   *   ->  *   ->  *   ->  ->  *
                                        |
            <-  <-  <-  <-  <-  <-  <-   
            |
uwin        *   ->  *   ->  *   ->  ->  *
'''

import copy, curses
import httplib2
import locale, lrc
import mpd
import os
import pyosd
import re
import select, sys
import time, threading, ttplyrics
import urllib

# ------------------------------
# global configuration
# modify as needed before startup
# ------------------------------

MPD_HOST = 'localhost'
MPD_PORT = 6600
ENABLE_OSD = False
ENABLE_RATING = True
LYRICS_DIR = os.path.join(os.path.expanduser('~'), '.mpy/lyrics')

# ------------------------------
# DON'T modify code below
# ------------------------------

class MPY_MOD():
    '''Base class of all mods'''

    def __init__(self, win, main):
        '''Initializer.
        
        Parameters:

            win - curses.window.
            main - main control. MPY instance.'''

        self.win = win
        self.main = main
        self.mpc = main.mpc
        self.board = main.board
        self.height, self.width = self.win.getmaxyx()

        self.nsks = []
        self.psks = []

    def udata(self):
        '''Update data.'''
        
        self.status = self.main.status
        self.stats = self.main.stats
        self.currentsong = self.main.currentsong

    def round_one(self, c):
        '''Round one.'''

        pass

    def round_two(self):
        '''Round two.'''

        pass

    def uwin(self):
        '''Update window.'''

        pass

    def _bar_rdis(self, y, x):
        '''Reset display for bar-like windows.'''

        self.win.resize(1, self.main.width)
        self.height, self.width = self.win.getmaxyx()
        self.win.mvwin(y, x)

    def _block_rdis(self):
        '''Reset display for block-like windows.'''

        self.win.resize(self.main.height - 4, self.main.width)
        self.height, self.width = self.win.getmaxyx()
        self.win.mvwin(2, 0)

        self.sel = min(self.sel, self.beg + self.height - 1)

    def rdis(self):
        '''Reset display use _bar_rdis/_block_rdis.'''

        pass

    def _format_time(self, tm):
        '''Convert time: <seconds> -> <hh:mm:ss>.'''

        if tm:
            tm = int(tm)
            h, m, s = tm // 3600, (tm // 60) % 60, tm % 60
            if h > 0:
                return '{}:{:02d}:{:02d}'.format(h, m, s)
            else:
                return '{:02d}:{:02d}'.format(m, s)
        else:
            return ''

    def _validate(self, n):
        '''Constrain value in range [0, num).'''

        return max(min(n, self.num - 1), 0)

    def _search(self, modname, c):
        '''Search in mods.'''

        if modname == 'Queue':
            items = self._queue
        elif modname in ['Database', 'Artist-Album', 'Search']:
            items = self._view

        if self.main.search and self.main.search_di:
            di = {
                    ord('/') : 1, 
                    ord('?') : -1, 
                    ord('n') : self.main.search_di, 
                    ord('N') : -self.main.search_di
                    }[c]
            has_match = False

            for i in [k % len(items) for k in range(self.sel + di, self.sel + di + di * len(items), di)]:
                item = items[i]

                if modname in ['Queue', 'Artist-Album', 'Search']:
                    title = item.get('title') or os.path.basename(item['file'])
                elif modname == 'Database':
                    title = item.values()[0]

                if title.find(self.main.search) != -1:
                    has_match = True
                    if di == 1 and i <= self.sel:
                        self.board['msg'] = 'search hit BOTTOM, continuing at TOP'
                    elif di == -1 and i >= self.sel:
                        self.board['msg'] = 'search hit TOP, continuing at BOTTOM'
                    self.locate(i)
                    break

            if not has_match:
                self.board['msg'] = 'Pattern not found: {}'.format(self.main.search)

class MPY_SCROLL():
    '''Scrolling interface.
    
    'ns_' means no selection'''

    def __init__(self):
        self.beg = 0
        self.num = 0
        self.cur = 0
        self.sel = 0

    def one_line_down(self):
        if self.sel < self.num - 1:
            self.sel += 1
            if self.sel - self.beg == self.height:
                self.beg += 1

    def one_line_up(self):
        if self.sel > 0:
            self.sel -= 1
            if self.sel - self.beg == -1:
                self.beg -= 1

    def one_page_down(self):
        if self.sel < self.num - self.height:
            self.sel += self.height
            self.beg = min(self.beg + self.height, self.num - self.height)
        else:
            self.sel = self.num - 1
            self.beg = max(self.num - self.height, 0)

    def one_page_up(self):
        if self.sel < self.height:
            self.sel = 0
            self.beg = 0
        else:
            self.sel -= self.height
            self.beg = max(self.beg - self.height, 0)

    def ns_one_line_down(self):
        if self.beg < self.num - self.height:
            self.beg += 1

    def ns_one_line_up(self):
        if self.beg > 0:
            self.beg -= 1

    def ns_one_page_down(self):
        self.beg = min(self.beg + self.height, self.num - self.height)

    def ns_one_page_up(self):
        self.beg = max(self.beg - self.height, 0)

    def to_top(self):
        self.sel = self.beg

    def to_middle(self):
        self.sel = min(self.beg + self.height / 2, self.num - 1)

    def to_bottom(self):
        self.sel = min(self.beg + self.height - 1, self.num - 1)

    def to_begin(self):
        self.beg = 0
        self.sel = 0

    def to_end(self):
        self.beg = self.num - 1
        self.sel = self.num - 1

    def locate(self, pos):
        '''Locate sel at pos, and put in the center.'''

        if pos >= self.height / 2:
            self.beg = pos - self.height / 2
        else:
            self.beg = 0
        self.sel = pos

class MPY_MENU(MPY_MOD):
    '''Display mod name, play mode and volume.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        self.win.attron(curses.A_BOLD)

    def _make_mode_str(self):
        '''Prepare mode_str.'''

        blank = ' ' * 5
        return (int(self.status['consume']) and '[con]' or blank) + \
                (int(self.status['random']) and '[ran]' or blank) + \
                (int(self.status['repeat']) and '[rep]' or blank) + \
                (int(self.status['single']) and '[sin]' or blank)

    def _make_menu_str(self):
        title_str = self.main.tmodname
        mode_str = self._make_mode_str()
        vol_str = 'Volume: ' + self.status['volume'] + '%'
        state_str = '{mode}    {vol}'.format(mode=mode_str, vol=vol_str)
        title_len = self.width - len(state_str)
        return title_str[:title_len].ljust(title_len) + state_str

    def uwin(self):
        menu_str = self._make_menu_str()

        # must use insstr instead of addstr, since addstr cannot 
        # draw to the last character (will raise an exception). 
        # Similar cases follow in other mods.
        self.win.erase()
        self.win.insstr(0, 0, menu_str)
        self.win.noutrefresh()

    def rdis(self):
        self._bar_rdis(0, 0)

class MPY_TITLE(MPY_MOD):
    '''Hline.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)

    def uwin(self):
        self.win.erase()
        self.win.insstr(0, 0, self.width * '-')
        self.win.noutrefresh()

    def rdis(self):
        self._bar_rdis(1, 0)

class MPY_PROGRESS(MPY_MOD):
    '''Show playing progress.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)

    def _make_prog_str(self):
        '''Prepare prog_str.'''

        # no 'time' option in mpd's status if stopped
        tm = self.status.get('time')
        if tm:
            elapsed, total = tm.split(':')
            pos = int((float(elapsed) / float(total)) * (self.width - 1))
            return '=' * pos + '0' + '-' * (self.width - pos - 1)
        else:
            return '-' * self.width

    def uwin(self):
        prog_str = self._make_prog_str()

        self.win.erase()
        self.win.insstr(0, 0, prog_str)
        self.win.noutrefresh()

    def rdis(self):
        self._bar_rdis(self.main.height - 2, 0)

class MPY_STATUS(MPY_MOD):
    '''Show playing status, elapsed/total time.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        self._state_name = {
                'play' : 'Playing',
                'stop' : 'Stopped',
                'pause' : 'Paused',
                }
        self.win.attron(curses.A_BOLD)

    def _make_title_str(self):
        '''Prepare title_str.'''

        song = self.currentsong
        title = song and (song.get('title') or os.path.basename(song.get('file'))) or ''
        return '{} > {}'.format(self._state_name[self.status['state']], title)

    def _make_tm_str(self):
        '''Prepare tm_str.'''

        tm = self.status.get('time') or '0:0'
        elapsed, total = tm.split(':')
        elapsed, total = int(elapsed), int(total)
        elapsed_mm, elapsed_ss, total_mm, total_ss = elapsed / 60, elapsed % 60, total / 60, total % 60
        return '[{0}:{1:02d} ~ {2}:{3:02d}]'.format(elapsed_mm, elapsed_ss, total_mm, total_ss)

    def uwin(self):
        # use two strs because it's difficult to calculate 
        # display length of unicode characters
        title_str = self._make_title_str()
        tm_str = self._make_tm_str()

        self.win.erase()
        self.win.insstr(0, 0, title_str)
        self.win.insstr(0, self.width - len(tm_str), tm_str)
        self.win.noutrefresh()

    def rdis(self):
        self._bar_rdis(self.main.height - 1, 0)

class MPY_MESSAGE(MPY_MOD):
    '''Show message and get user input.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        self._msg = None
        self._timeout = 0

    def getstr(self, prompt):
        '''Get user input with prompt <prompt>.'''

        curses.nocbreak()
        curses.echo()
        curses.curs_set(1)
        self.win.move(0, 0)
        self.win.clrtoeol()
        self.win.addstr('{}: '.format(prompt), curses.A_BOLD)
        s = self.win.getstr(0, len(prompt) + 2)
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        return s

    def uwin(self):
        msg = self.board.get('msg')
        if msg:
            self._msg = msg
            self._timeout = 5

        if self._timeout > 0:
            self.win.erase()
            self.win.insstr(0, 0, self._msg, curses.A_BOLD)
            self.win.noutrefresh()
            self._timeout -= 1

    def rdis(self):
        self._bar_rdis(self.main.height - 1, 0)

class MPY_HELP(MPY_MOD, MPY_SCROLL):
    '''Help.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)
        self._options = [
                ('group', 'Global', None), 
                ('hline', None, None), 
                ('item', 'F1', 'Help'), 
                ('item', 'F2', 'Queue'), 
                ('item', 'F3', 'Database'), 
                ('item', 'F4', 'Lyrics'), 
                ('item', 'F5', 'Artist-Album'), 
                ('item', 'F6', 'Search'), 
                ('blank', None, None), 
                ('item', 'q', 'quit'), 
                ('blank', None, None), 

                ('group', 'Playback', None), 
                ('hline', None, None), 
                ('item', 'Space', 'Play/Pause'), 
                ('item', 's', 'Stop'), 
                ('item', '>', 'next song'), 
                ('item', '<', 'previous song'), 
                ('blank', None, None), 
                ('item', 'u', 'consume mode'), 
                ('item', 'i', 'random mode'), 
                ('item', 'o', 'repeat mode'), 
                ('item', 'p', 'single mode'), 
                ('blank', None, None), 
                ('item', '9', 'volume down'), 
                ('item', '0', 'volume up'), 
                ('blank', None, None), 
                ('item', 'left', 'seek +1'), 
                ('item', 'right', 'seek -1'), 
                ('item', 'down', 'seek -1%'), 
                ('item', 'up', 'seek +1%'), 
                ('blank', None, None), 

                ('group', 'Movement', None), 
                ('hline', None, None), 
                ('item', 'j', 'go one line down'), 
                ('item', 'k', 'go one line up'), 
                ('item', 'f', 'go one page down'), 
                ('item', 'b', 'go one page up'), 
                ('item', 'g', 'go to top of list'), 
                ('item', 'G', 'go to bottom of list'), 
                ('item', 'H', 'go to top of screen'), 
                ('item', 'M', 'go to middle of screen'), 
                ('item', 'L', 'go to bottom of screen'), 
                ('blank', None, None), 
                ('item', '/', 'search down'), 
                ('item', '?', 'search up'), 
                ('item', 'n', 'next match'), 
                ('item', 'N', 'previous match'), 
                ('blank', None, None), 

                ('group', 'Queue', ''), 
                ('hline', None, None), 
                ('item', 'Enter', 'Play'), 
                ('item', 'l', 'select and center current song'), 
                ('item', '\'', 'toggle auto center'), 
                ('item', ';', 'locate selected song in database'), 
                ('item', 'h', 'get info about current/selected song'), 
                ('blank', None, None), 
                ('item', '1', 'rate selected song as     *'), 
                ('item', '2', 'rate selected song as    **'), 
                ('item', '3', 'rate selected song as   ***'), 
                ('item', '4', 'rate selected song as  ****'), 
                ('item', '5', 'rate selected song as *****'), 
                ('blank', None, None), 
                ('item', 'J', 'Move down selected song'), 
                ('item', 'K', 'Move up selected song'), 
                ('item', 'e', 'shuffle queue'), 
                ('item', 'c', 'clear queue'), 
                ('item', 'a', 'add all songs from database'), 
                ('item', 'd', 'delete selected song from queue'), 
                ('item', 'S', 'save queue to playlist'), 
                ('item', 'O', 'load queue from playlist'), 
                ('blank', None, None), 

                ('group', 'Database', ''), 
                ('hline', None, None), 
                ('item', 'Enter', 'open directory / append to queue (if not existing yet) and play / load playlist'), 
                ('item', '\'', 'go to parent directory'), 
                ('item', '"', 'go to root directory'), 
                ('item', 'a', 'append song to queue recursively'), 
                ('item', ';', 'locate selected song in queue'), 
                ('item', 'h', 'get info about selected song'), 
                ('item', 'U', 'update database'), 
                ('blank', None, None), 

                ('group', 'Lyrics', ''), 
                ('hline', None, None), 
                ('item', 'l', 'center current line'), 
                ('item', '\'', 'toggle auto center'), 
                ('item', 'K', 'save lyrics'), 
                ('blank', None, None), 

                ('group', 'Artist-Album', ''), 
                ('hline', None, None), 
                ('item', 'Enter', 'open level / append to queue (if not existing yet) and play'), 
                ('item', '\'', 'go to parent level'), 
                ('item', '"', 'go to root level'), 
                ('item', 'a', 'append song to queue recursively'), 
                ('item', ';', 'locate selected song in queue'), 
                ('blank', None, None), 

                ('group', 'Search', ''), 
                ('hline', None, None), 
                ('item', 'B', 'start a database search, syntax = <tag_name>:<tag_value>'), 
                ('item', 'Enter', 'append to queue (if not existing yet) and play'), 
                ('item', 'a', 'append to queue'), 
                ('item', ';', 'locate selected song in queue'), 
                ('blank', None, None), 

                ('group', 'Info', ''), 
                ('hline', None, None), 
                ('item', 'h', 'back to previous window'), 
                ('blank', None, None), 
                ]
        self.num = len(self._options)

    def round_one(self, c):
        if c == ord('j'):
            self.ns_one_line_down()
        elif c == ord('k'):
            self.ns_one_line_up()
        elif c == ord('f'):
            self.ns_one_page_down()
        elif c == ord('b'):
            self.ns_one_page_up()

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            line = self._options[i]
            if line[0] == 'group':
                self.win.insstr(i - self.beg, 6, line[1], curses.A_BOLD)
            elif line[0] == 'hline':
                self.win.attron(curses.A_BOLD)
                self.win.hline(i - self.beg, 3, '-', self.width - 6)
                self.win.attroff(curses.A_BOLD)
            elif line[0] == 'item':
                self.win.insstr(i - self.beg, 0, line[1].rjust(20) + ' : ' + line[2])
            elif line[0] == 'blank':
                pass
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY_QUEUE(MPY_MOD, MPY_SCROLL):
    '''Queue = current playlist.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)

        self.nsks = [ord('l'), ord('\'')]
        self.psks = [ord('d'), ord('J'), ord('K')]

        # playlist version
        self._version = -1

        # auto-center
        self._auto_center = False

    def udata(self):
        MPY_MOD.udata(self)
        
        if self.main.c in self.main.allpsks:
            return

        # fetch playlist if version is different
        if self._version != int(self.main.status['playlist']):
            self._queue = self.mpc.playlistinfo()
            self.num = len(self._queue)
            self.beg = self._validate(self.beg)
            self.sel = self._validate(self.sel)
            # self.cur is set later, so not validated here

            for song in self._queue:
                if ENABLE_RATING:
                    try:
                        rating = int(self.mpc.sticker_get('song', song['file'], 'rating').split('=',1)[1])
                    except mpd.CommandError:
                        rating = 0
                    finally:
                        song['rating'] = rating
                else:
                    song['rating'] = 0

            self._version = int(self.status['playlist'])

        self.cur = self.status.has_key('song') and int(self.status['song']) or 0

    def round_one(self, c):
        if c == ord('j'):
            self.one_line_down()
        elif c == ord('k'):
            self.one_line_up()
        elif c == ord('f'):
            self.one_page_down()
        elif c == ord('b'):
            self.one_page_up()
        elif c == ord('H'):
            self.to_top()
        elif c == ord('M'):
            self.to_middle()
        elif c == ord('L'):
            self.to_bottom()
        elif c == ord('g'):
            self.to_begin()
        elif c == ord('G'):
            self.to_end()
        elif c == ord('l'):
            self.locate(self.cur)
        elif c == ord('a'):
            self.mpc.add('')
        elif c == ord('c'):
            self.mpc.clear()
            self.num, self.beg, self.sel, self.cur = 0, 0, 0, 0
        elif c == ord('d'):
            if self.num > 0:
                self.main.pending.append('deleteid({})'.format(self._queue[self.sel]['id']))
                self._queue.pop(self.sel)
                if self.sel < self.cur:
                    self.cur -= 1
                self.num -= 1
                self.beg = self._validate(self.beg)
                self.sel = self._validate(self.sel)
                self.cur = self._validate(self.cur)
        elif c == ord('J'):
            if self.sel + 1 < self.num:
                self.main.pending.append('swap({}, {})'.format(self.sel, self.sel + 1))
                self._queue[self.sel], self._queue[self.sel + 1] = self._queue[self.sel + 1], self._queue[self.sel]
                if self.cur == self.sel:
                    self.cur += 1
                elif self.cur == self.sel + 1:
                    self.cur -= 1
                self.one_line_down()
        elif c == ord('K'):
            if self.sel > 0:
                self.main.pending.append('swap({}, {})'.format(self.sel, self.sel - 1))
                self._queue[self.sel - 1], self._queue[self.sel] = self._queue[self.sel], self._queue[self.sel - 1]
                if self.cur == self.sel - 1:
                    self.cur += 1
                elif self.cur == self.sel:
                    self.cur -= 1
                self.one_line_up()
        elif c == ord('e'):
            self.mpc.shuffle()
        elif c == ord('\n'):
            self.mpc.playid(self._queue[self.sel]['id'])
        elif c in range(ord('1'), ord('5') + 1):
            if ENABLE_RATING:
                rating = c - ord('0')
                song = self._queue[self.cur]
                self.mpc.sticker_set('song', song['file'], 'rating', rating)
                song['rating'] = rating
        elif c in [ord('/'), ord('?'), ord('n'), ord('N')]:
            self._search('Queue', c)
        elif c == ord('\''):
            self._auto_center = not self._auto_center
        elif c == ord(';'):
            self.board['path'] = self._queue[self.sel]['file']

        # set q_sel in shared memory (INFO mod will use)
        if self.num > 0:
            self.board['q_sel'] = self._queue[self.sel]
            

    def round_two(self):
        uri = self.board.get('locate')
        if uri:
            for i in range(len(self._queue)):
                if uri == self._queue[i]['file']:
                    self.locate(i)
                    break
            else:
                self.board['msg'] = 'Not found in playlist'

        # auto center
        if self._auto_center:
            self.locate(self.cur)

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self._queue[i]
            title = item.has_key('title') and item['title'] or os.path.basename(item['file'])
            rating = item['rating']
            tm = self._format_time(item['time'])

            if i == self.cur:
                self.win.attron(curses.A_BOLD)
            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.addnstr(i - self.beg, 0, title, self.width - 18)
            self.win.addnstr(i - self.beg, self.width - 16, rating * '*', 5)
            self.win.insstr(i - self.beg, self.width - len(tm), tm)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
            if i == self.cur:
                self.win.attroff(curses.A_BOLD)
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY_DATABASE(MPY_MOD, MPY_SCROLL):
    '''All songs/directories/playlists in database.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)

        # current displayed dir
        self._dir = ''
        self._view = self._build_view()

    def _build_view(self, keeppos=False):
        '''Build view using self._dir.
        
        A view is rebuilt when self._dir changes (ex. database update), 
        or new items are added/removed (ex. playlist add/delete).'''

        view = self.mpc.lsinfo(self._dir)
        view.insert(0, {'directory' : '..'})
        self.num = len(view)
        if keeppos:
            self.beg = self._validate(self.beg)
            self.sel = self._validate(self.sel)
        else:
            self.beg = 0
            self.sel = 0
        return view

    def round_one(self, c):
        if c == ord('j'):
            self.one_line_down()
        elif c == ord('k'):
            self.one_line_up()
        elif c == ord('f'):
            self.one_page_down()
        elif c == ord('b'):
            self.one_page_up()
        elif c == ord('H'):
            self.to_top()
        elif c == ord('M'):
            self.to_middle()
        elif c == ord('L'):
            self.to_bottom()
        elif c == ord('g'):
            self.to_begin()
        elif c == ord('G'):
            self.to_end()
        elif c == ord('\''):
            old_dir = self._dir
            self._dir = os.path.dirname(self._dir)
            self._view = self._build_view()
            for i in range(len(self._view)):
                if self._view[i].get('directory') == old_dir:
                    self.locate(i)
                    break
        elif c == ord('"'):
            self._dir = ''
            self._view = self._build_view()
        elif c == ord('\n'):
            item = self._view[self.sel]
            if item.has_key('directory'):
                uri = item['directory']
                if uri == '..':
                    old_dir = self._dir
                    self._dir = os.path.dirname(self._dir)
                    self._view = self._build_view()
                    for i in range(len(self._view)):
                        if self._view[i].get('directory') == old_dir:
                            self.locate(i)
                            break
                else:
                    self._dir = uri
                    self._view = self._build_view()

            elif item.has_key('file'):
                uri = item['file']
                songs = self.mpc.playlistfind('file', uri)
                if songs:
                    self.mpc.playid(songs[0]['id'])
                else:
                    self.mpc.add(uri)
                    song = self.mpc.playlistfind('file', uri)[0]
                    self.mpc.playid(song['id'])
            elif item.has_key('playlist'):
                name = item['playlist']
                try:
                    self.mpc.load(name)
                except mpd.CommandError as e:
                    self.board['msg'] = str(e).rsplit('} ')[1]
                else:
                    self.board['msg'] = 'Playlist {} loaded'.format(name)
        elif c == ord('a'):
            item = self._view[self.sel]
            if item.has_key('directory'):
                uri = item['directory']
            else:
                uri = item['file']
            if uri == '..':
                self.mpc.add(os.path.dirname(self._dir))
            else:
                self.mpc.add(uri)
        elif c == ord('d'):
            item = self._view[self.sel]
            if item.has_key('playlist'):
                name = item['playlist']
                try:
                    self.mpc.rm(name)
                except mpd.CommandError as e:
                    self.board['msg'] = str(e).rsplit('} ')[1]
                else:
                    self.board['msg'] = 'Playlist {} deleted'.format(name)
                    self._view = self._build_view(keeppos=True)
        elif c == ord('U'):
            self.mpc.update()
            self._dir = ''
            self._view = self._build_view()
            self.board['msg'] = 'Database updated'
        elif c in [ord('/'), ord('?'), ord('n'), ord('N')]:
            self._search('Database', c)
        elif c == ord(';'):
            # tell QUEUE we want to locate a song
            item = self._view[self.sel]
            if item.has_key('file'):
                self.board['locate'] = item.get('file')
            else:
                self.board['msg'] = 'No song selected'

        # set d_sel in shared memory (INFO mod will use)
        self.board['d_sel'] = self._view[self.sel].get('file')

    def round_two(self):
        # if there's a path request, rebuild view, using 
        # dirname(path) as display root, and search for the 
        # requested song.
        uri = self.board.get('path')
        if uri:
            self._dir = os.path.dirname(uri)
            self._view = self._build_view()
            for i in range(len(self._view)):
                if self._view[i].get('file') == uri:
                    self.locate(i)
                    break
            else:
                self.board['msg'] = 'Not found in database'

        # if a playlist is saved, rebuild view, keep original positions
        if self.board.get('main-playlist') == 'saved':
            self._view = self._build_view(keeppos=True)

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self._view[i]
            if item.has_key('directory'):
                t, uri = 'directory', item['directory']
            elif item.has_key('file'):
                t, uri = 'file', item['file']
            elif item.has_key('playlist'):
                t, uri = 'playlist', item['playlist']

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            if t == 'directory':
                self.win.attron(curses.color_pair(1) | curses.A_BOLD)
            elif t == 'playlist':
                self.win.attron(curses.color_pair(2) | curses.A_BOLD)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, os.path.basename(uri))
            if t == 'directory':
                self.win.attroff(curses.color_pair(1) | curses.A_BOLD)
            elif t == 'playlist':
                self.win.attroff(curses.color_pair(2) | curses.A_BOLD)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY_LYRICS(MPY_MOD, MPY_SCROLL, threading.Thread):
    '''Display lyrics.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)
        threading.Thread.__init__(self, name='lyrics')

        # directory to save lyrics. 
        # Make sure have write permission.
        self._lyrics_dir = LYRICS_DIR

        # new song, maintained by module
        self._nsong = None
        # old song, maintained by worker
        self._osong = None
        # title of lyrics to fetch
        self._title = None
        # artist of lyrics to fetch
        self._artist = None
        # current lyrics, oneline str
        self._lyrics = '[00:00.00]Cannot fetch lyrics (No artist/title).'
        # current lyrics timestamp as lists, used by main thread only
        self._ltimes = []
        # current lyrics text as lists, used by main thread only
        self._ltexts = []
        # incicate lyrics state: 'local', 'net', 'saved' or False
        self._lyrics_state = False
        # condition variable for lyrics fetching and display
        self._cv = threading.Condition()

        # auto-center
        self._auto_center = True

        # osd engine
        if ENABLE_OSD:
            self._osd = pyosd.osd(font='-misc-droid sans mono-medium-r-normal--0-0-0-0-m-0-iso8859-1', 
                    colour='#FFFF00', 
                    align=pyosd.ALIGN_CENTER, 
                    pos=pyosd.POS_TOP, 
                    timeout=-1)
            # remembered for osd
            self._osdcur = -1

    def _transtag(self, tag):
        '''Transform tag into format used by lrc engine.'''

        if tag is None:
            return None
        else:
            return tag.replace(' ', '').lower()

    def udata(self):
        MPY_MOD.udata(self)

        song = self.currentsong

        # do nothing if cannot acquire lock
        if self._cv.acquire(blocking=False):
            self._nsong = song.get('file')
            # if currengsong changes, wake up worker
            if self._nsong != self._osong:
                self._artist = song.get('artist')
                self._title = song.get('title')
                self._cv.notify()
            self._cv.release()

    def _save_lyrics(self):
        if self._artist and self._title and self._cv.acquire(blocking=False):
            with open(os.path.join(self._lyrics_dir, self._artist.replace('/', '_') + '-' + self._title.replace('/', '_') + '.lrc'), 'wt') as f:
                f.write(self._lyrics)
            self.board['msg'] = 'Lyrics {}-{}.lrc saved.'.format(self._artist, self._title)
            self._lyrics_state = 'saved'
            self._cv.release()
        else:
            self.board['msg'] = 'Lyrics saving failed.'

    def round_one(self, c):
        if c == ord('j'):
            self.ns_one_line_down()
        elif c == ord('k'):
            self.ns_one_line_up()
        elif c == ord('f'):
            self.ns_one_page_down()
        elif c == ord('b'):
            self.ns_one_page_up()
        elif c == ord('l'):
            self.locate(self.cur)
        elif c == ord('\''):
            self._auto_center = not self._auto_center
        elif c == ord('K'):
            self._save_lyrics()

    def _parse_lrc(self, lyrics):
        '''Parse lrc lyrics into ltimes and ltexts.'''

        tags, tms = lrc.parse(lyrics)
        sorted_keys = sorted(tms.keys())
        ltimes = [int(i) for i in sorted_keys]
        ltexts = [tms.get(i) for i in sorted_keys]
        return ltimes, ltexts

    def current_line(self):
        '''Calculate line number of current progress.'''

        cur = 0
        tm = self.status.get('time')
        if tm:
            elapsed = int(tm.split(':')[0])
            while cur < self.num and self._ltimes[cur] <= elapsed:
                cur += 1
            cur -= 1
        return cur

    def round_two(self):
        # output 'Updating...' if cannot acquire lock
        if self._cv.acquire(blocking=0):
            # if worker reports lyrics fetched
            if self._lyrics_state in ['local', 'net']:
                # parse lrc (and copy lrc from shared mem to non-shared mem)
                self._ltimes, self._ltexts = self._parse_lrc(self._lyrics)
                self.num, self.beg = len(self._ltimes), 0

                # auto-save lyrics
                if self._lyrics_state == 'net' and self.num > 10:
                    self._save_lyrics()
                else:
                    self._lyrics_state = 'saved'

                if ENABLE_OSD:
                    self._osdcur = -1
            self._cv.release()
        else:
            self._ltimes, self._ltexts = [0], ['Updating...']
            # set self.num and self.beg
            self.num, self.beg = 1, 0

        # set self.cur, the highlighted line
        self.cur = self.current_line()

        # auto center
        if self._auto_center:
            self.locate(self.cur)

    def uwin(self):
        self.win.erase()
        attr = curses.A_BOLD | curses.color_pair(3)
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            if i == self.cur:
                self.win.insstr(i - self.beg, 0, self._ltexts[i], attr)
            else:
                self.win.insstr(i - self.beg, 0, self._ltexts[i])
        self.win.noutrefresh()

        # osd lyrics if ENABLE_OSD is True
        if ENABLE_OSD:
            if self.cur != self._osdcur:
                self._osd.hide()
                if self._ltexts:
                    self._osd.display(self._ltexts[self.cur])
                self._osdcur = self.cur

    def run(self):
        self._cv.acquire()
        while True:
            # wait if currentsong doesn't change
            while self._nsong == self._osong:
                self._cv.wait()

            self._lyrics = '[00:00.00]Cannot fetch lyrics (No artist/title).'
            self._lyrics_state = 'local'

            # fetch lyrics if required information is provided
            if self._artist and self._title:
                # try to fetch from local lrc
                lyrics_file = os.path.join(self._lyrics_dir, self._artist.replace('/', '_') + '-' + self._title.replace('/', '_') + '.lrc')
                if os.path.isfile(lyrics_file):
                    with open(lyrics_file, 'rt') as f:
                        self._lyrics = f.read()
                    # inform round_two: lyrics has been fetched
                    self._lyrics_state = 'local'
                # if local lrc doesn't exist, fetch from Internet
                else:
                    self._lyrics = ttplyrics.fetch_lyrics(self._transtag(self._artist), self._transtag(self._title))
                    # inform round_two: lyrics has been fetched
                    self._lyrics_state = 'net'
            self._osong = self._nsong

    def rdis(self):
        self._block_rdis()

class MPY_INFO(MPY_MOD, MPY_SCROLL):
    '''Information about songs:
    
        currently playing
        currently selected in queue
        currently selected in database'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)
        self._prevtmodname = None
        # current playing
        self._cp = {}
        # selected in queue
        self._siq = {}
        # selected in database
        self._sid = {}
        # database.sel's uri cache
        self._dburi = None
        self._options = [
                ('group', 'Currently Playing', None), 
                ('hline', None, None), 
                ('item', 'Title', ''), 
                ('item', 'Artist', ''), 
                ('item', 'Album', ''), 
                ('item', 'Track', ''), 
                ('item', 'Genre', ''), 
                ('item', 'Date', ''), 
                ('item', 'Time', ''), 
                ('item', 'File', ''), 
                ('blank', None, None), 

                ('group', 'Currently Selected in Queue', None), 
                ('hline', None, None), 
                ('item', 'Title', ''), 
                ('item', 'Artist', ''), 
                ('item', 'Album', ''), 
                ('item', 'Track', ''), 
                ('item', 'Genre', ''), 
                ('item', 'Date', ''), 
                ('item', 'Time', ''), 
                ('item', 'File', ''), 
                ('blank', None, None), 

                ('group', 'Currently Selected in Database', None), 
                ('hline', None, None), 
                ('item', 'Title', ''), 
                ('item', 'Artist', ''), 
                ('item', 'Album', ''), 
                ('item', 'Track', ''), 
                ('item', 'Genre', ''), 
                ('item', 'Date', ''), 
                ('item', 'Time', ''), 
                ('item', 'File', ''), 
                ('blank', None, None), 

                ('group', 'MPD Statistics', None), 
                ('hline', None, None), 
                ('item', 'NumberofSongs', ''), 
                ('item', 'NumberofArtists', ''), 
                ('item', 'NumberofAlbums', ''), 
                ('item', 'Uptime', ''), 
                ('item', 'Playtime', ''), 
                ('item', 'DBPlaytime', ''), 
                ('item', 'DBUpdateTime', ''), 
                ('blank', None, None), 
                ]
        self._options_d = None
        self._song_key_list = ['Title', 'Artist', 'Album', 'Track', 'Genre', 'Date', 'Time', 'File']
        self._stats_key_list = ['Songs', 'Artists', 'Albums', 'Uptime', 'Playtime', 'DB_Playtime', 'DB_Update']

    def round_one(self, c):
        if c == ord('j'):
            self.ns_one_line_down()
        elif c == ord('k'):
            self.ns_one_line_up()
        elif c == ord('f'):
            self.ns_one_page_down()
        elif c == ord('b'):
            self.ns_one_page_up()
        elif c == ord('h'):
            if self._prevtmodname:
                self.board['i_back'] = self._prevtmodname

    def round_two(self):
        if self.board.has_key('prevtmodname'):
            self._prevtmodname = self.board['prevtmodname']

        # get song info.

        # cp = currently playing
        # siq = selected in queue
        # sid = selected in database

        # on success, _cp and _siq are nonempty dicts.
        # on failure, _cp and _siq are empty dicts.
        self._cp = self.currentsong
        try:
            self._siq = self.board.get('q_sel') or {}
        except (mpd.CommandError, IndexError):
            self._siq = {}
        try:
            uri = self.board.get('d_sel')
            if uri and uri != self._dburi and not self.main.idle:
                self._sid = self.mpc.listallinfo(uri)[0]
        except (mpd.CommandError, IndexError):
            self._sid = {}

        # setup sub lists
        cp_list = [('item', k, self._cp.get(k.lower()) or '') for k in self._song_key_list]
        siq_list = [('item', k, self._siq.get(k.lower()) or '') for k in self._song_key_list]
        sid_list = [('item', k, self._sid.get(k.lower()) or '') for k in self._song_key_list]
        stats_list = [('item', k, self.stats.get(k.lower()) or '') for k in self._stats_key_list]

        # format time
        for l in (cp_list, siq_list, sid_list):
            l[6] = (l[6][0], l[6][1], self._format_time(l[6][2]))
        for i in range(3, 6):
            stats_list[i] = (stats_list[i][0], stats_list[i][1], self._format_time(stats_list[i][2]))
        stats_list[6] = (stats_list[6][0], stats_list[6][1], time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(stats_list[6][2]))))

        # merge into main list
        self._options[2:10] = cp_list
        self._options[13:21] = siq_list
        self._options[24:32] = sid_list
        self._options[35:42] = stats_list

        # set up options display
        self._options_d = self._options[:]
        # breakup file paths
        for k in (31, 20, 9):
            self._options_d[k:k+1] = [('item', '', '/' + i) for i in self._options[k][2].split('/')]
            self._options_d[k] = ('item', 'File', self._options_d[k][2][1:])

        self.num = len(self._options_d)

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            line = self._options_d[i]
            if line[0] == 'group':
                self.win.insstr(i - self.beg, 6, line[1], curses.A_BOLD)
            elif line[0] == 'hline':
                self.win.attron(curses.A_BOLD)
                self.win.hline(i - self.beg, 3, '-', self.width - 6)
                self.win.attroff(curses.A_BOLD)
            elif line[0] == 'item':
                self.win.insstr(i - self.beg, 0, line[1].rjust(20) + ' : ' + line[2])
            elif line[0] == 'blank':
                pass
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY_ARTIST_ALBUM(MPY_MOD, MPY_SCROLL):
    '''List artists/albums in database.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)

        # current displayed dir
        self._type = 'artist'
        self._artist = None
        self._album = None
        self._view = self._build_view()

    def _build_view(self):
        '''Build view using self._type, self._artist and self._album.
        
        A view is rebuilt when self._type changes.'''

        if self._type == 'artist':
            view = self.mpc.list('artist')
        elif self._type == 'album':
            view = self._artist and self.mpc.list('album', self._artist) or []
        elif self._type == 'song':
            view = self._album and self.mpc.find('album', self._album) or []

        self.num = len(view)
        self.beg = 0
        self.sel = 0
        return view

    def round_one(self, c):
        if c == ord('j'):
            self.one_line_down()
        elif c == ord('k'):
            self.one_line_up()
        elif c == ord('f'):
            self.one_page_down()
        elif c == ord('b'):
            self.one_page_up()
        elif c == ord('H'):
            self.to_top()
        elif c == ord('M'):
            self.to_middle()
        elif c == ord('L'):
            self.to_bottom()
        elif c == ord('g'):
            self.to_begin()
        elif c == ord('G'):
            self.to_end()
        elif c == ord('\''):
            if self._type == 'artist':
                pass
            elif self._type == 'album':
                self._type = 'artist'
                self._view = self._build_view()
                for i in range(len(self._view)):
                    if self._view[i] == self._artist:
                        self.locate(i)
                        break
            elif self._type == 'song':
                self._type = 'album'
                self._view = self._build_view()
                for i in range(len(self._view)):
                    if self._view[i] == self._album:
                        self.locate(i)
                        break
        elif c == ord('"'):
            self._type = 'artist'
            self._view = self._build_view()
        elif c == ord('\n'):
            item = self._view[self.sel]
            if self._type == 'artist':
                self._artist = item
                self._type = 'album'
                self._view = self._build_view()
            elif self._type == 'album':
                self._album = item
                self._type = 'song'
                self._view = self._build_view()
            elif self._type == 'song':
                uri = item['file']
                songs = self.mpc.playlistfind('file', uri)
                if songs:
                    self.mpc.playid(songs[0]['id'])
                else:
                    self.mpc.add(uri)
                    song = self.mpc.playlistfind('file', uri)[0]
                    self.mpc.playid(song['id'])
        elif c == ord('a'):
            item = self._view[self.sel]
            if self._type == 'artist':
                self.mpc.findadd('artist', item)
            elif self._type == 'album':
                self.mpc.findadd('album', item)
            elif self._type == 'song':
                self.mpc.add(item['file'])
        elif c in [ord('/'), ord('?'), ord('n'), ord('N')]:
            self._search('Artist', c)
        elif c == ord(';'):
            # tell QUEUE we want to locate a song
            if self._type == 'song':
                item = self._view[self.sel]
                self.board['locate'] = item.get('file')
            else:
                self.board['msg'] = 'No song selected'

    def round_two(self):
        if self.board.has_key('Database Updated.'):
            self._type = 'artist'
            self._view = self._build_view()

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self._view[i]

            if self._type in ['artist', 'album']:
                val = item
            elif self._type == 'song':
                val = item.get('title') or os.path.basename(item.get('file'))

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            if self._type == 'artist':
                self.win.attron(curses.color_pair(1) | curses.A_BOLD)
            elif self._type == 'album':
                self.win.attron(curses.color_pair(2) | curses.A_BOLD)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, val)
            if self._type == 'artist':
                self.win.attroff(curses.color_pair(1) | curses.A_BOLD)
            elif self._type == 'album':
                self.win.attroff(curses.color_pair(2) | curses.A_BOLD)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY_SEARCH(MPY_MOD, MPY_SCROLL):
    '''Search in the database.'''

    def __init__(self, win, main):
        MPY_MOD.__init__(self, win, main)
        MPY_SCROLL.__init__(self)

        self._view = []

    def _build_view(self, kw):
        '''Build view using search keywords.'''
        
        try:
            name, value = kw.split(':', 1)
            view = self.mpc.find(name, value) or []
            if not view:
                self.board['msg'] = 'Nothing found :('
        except:
            view = []
            self.board['msg'] = 'Invalid Syntax >_< Syntax = <tag_name>:<tag_value>'

        self.num = len(view)
        self.beg = 0
        self.sel = 0
        return view

    def round_one(self, c):
        if c == ord('j'):
            self.one_line_down()
        elif c == ord('k'):
            self.one_line_up()
        elif c == ord('f'):
            self.one_page_down()
        elif c == ord('b'):
            self.one_page_up()
        elif c == ord('H'):
            self.to_top()
        elif c == ord('M'):
            self.to_middle()
        elif c == ord('L'):
            self.to_bottom()
        elif c == ord('g'):
            self.to_begin()
        elif c == ord('G'):
            self.to_end()
        elif c == ord('B'):
            self._view = self._build_view(self.main.e.getstr('Database Search'))
        elif c == ord('\n'):
            item = self._view[self.sel]
            uri = item['file']
            songs = self.mpc.playlistfind('file', uri)
            if songs:
                self.mpc.playid(songs[0]['id'])
            else:
                self.mpc.add(uri)
                song = self.mpc.playlistfind('file', uri)[0]
                self.mpc.playid(song['id'])
        elif c == ord('a'):
            item = self._view[self.sel]
            self.mpc.add(item['file'])
        elif c in [ord('/'), ord('?'), ord('n'), ord('N')]:
            self._search('Search', c)
        elif c == ord(';'):
            # tell QUEUE we want to locate a song
            if self.sel < self.num:
                item = self._view[self.sel]
                self.board['locate'] = item.get('file')
            else:
                self.board['msg'] = 'No song selected'

    def uwin(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self._view[i]

            val = item.get('title') or os.path.basename(item.get('file'))

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, val)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

    def rdis(self):
        self._block_rdis()

class MPY():
    '''Main controller.'''

    def _init_curses(self):
        self.stdscr = curses.initscr()
        curses.start_color()
        curses.use_default_colors()
        curses.noecho()
        curses.cbreak()
        curses.curs_set(0)
        curses.init_pair(1, curses.COLOR_BLUE, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        self.stdscr.keypad(1)
        self.stdscr.leaveok(1)

    def _init_mpd(self, host, port):
        self.mpc = mpd.MPDClient()
        self.mpc.connect(host, port)

    def _init_conf(self):
        '''Initialize internal configurations.'''

        # main configuration
        self.height, self.width = self.stdscr.getmaxyx()
        self.tmodname = 'Queue'
        self.loop = False
        self.idle = False
        self.seek = False
        self.sync = True
        self.elapsed = 0
        self.total = 0
        self.search = ''
        self.search_di = 0
        self.pending = []

        # no sync keys
        self.nsks = [
                ord('j'), ord('k'), ord('f'), ord('b'), 
                ord('H'), ord('M'), ord('L'), ord('g'), ord('G'), 
                curses.KEY_F1, curses.KEY_F2, curses.KEY_F3, curses.KEY_F4, 
                '/', '?', 'n', 'N', 
                -1
                ]
        # partial sync keys
        self.psks = [
                curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT
                ]

        # user input
        self.c = None

    def _init_data(self):
        self.status = self.mpc.status()
        self.stats = self.mpc.stats()
        self.currentsong = self.mpc.currentsong()

    def _init_board(self):
        self.board = {}

    def _init_mods(self):
        '''Initialize modules (mods).'''

        self.m = MPY_MENU(self.stdscr.subwin(1, self.width, 0, 0), self)                    # menu
        self.t = MPY_TITLE(self.stdscr.subwin(1, self.width, 1, 0), self)                   # title
        self.p = MPY_PROGRESS(self.stdscr.subwin(1, self.width, self.height - 2, 0), self)  # progress
        self.s = MPY_STATUS(self.stdscr.subwin(1, self.width, self.height - 1, 0), self)    # status
        self.e = MPY_MESSAGE(self.stdscr.subwin(1, self.width, self.height - 1, 0), self)   # message
        self.h = MPY_HELP(curses.newwin(self.height - 4, self.width, 2, 0), self)           # help
        self.q = MPY_QUEUE(curses.newwin(self.height - 4, self.width, 2, 0), self)          # queue
        self.d = MPY_DATABASE(curses.newwin(self.height - 4, self.width, 2, 0), self)       # database
        self.l = MPY_LYRICS(curses.newwin(self.height - 4, self.width, 2, 0), self)         # lyrics
        self.a = MPY_ARTIST_ALBUM(curses.newwin(self.height - 4, self.width, 2, 0), self)   # artist-album
        self.r = MPY_SEARCH(curses.newwin(self.height - 4, self.width, 2, 0), self)         # search
        self.i = MPY_INFO(curses.newwin(self.height - 4, self.width, 2, 0), self)           # info

        # module dict
        self.mdict = {
                'Menu' : self.m, 
                'Title' : self.t, 
                'Progress' : self.p, 
                'Status' : self.s, 
                'Message' : self.e, 
                'Help' : self.h, 
                'Queue' : self.q, 
                'Database' : self.d, 
                'Lyrics' : self.l, 
                'Artist-Album' : self.a, 
                'Search' : self.r, 
                'Info' : self.i, 
                }
        # module list
        self.mlist = self.mdict.values()

        # bar module dict
        self.bmdict = {
                'Menu' : self.m, 
                'Title' : self.t, 
                'Progress' : self.p, 
                'Status' : self.s, 
                'Message' : self.e, 
                }
        # bar module list
        self.bmlist = self.bmdict.values()

    def __enter__(self, host=MPD_HOST, port=MPD_PORT):
        self._init_curses()
        self._init_mpd(host, port)
        self._init_conf()
        self._init_data()
        self._init_board()
        self._init_mods()

        # start lyrics daemon thread
        self.l.daemon = True
        self.l.start()

        # initial update
        self.process(fd='init')

        return self

    def __exit__(self, type, value, traceback):
        curses.endwin()

    def udata(self):
        # update main data
        self.status = self.mpc.status()
        self.stats = self.mpc.stats()
        self.currentsong = self.mpc.currentsong()

        # update mods data
        for mod in self.mlist:
            mod.udata()

    def round_one(self, c):
        # seeking
        if c in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_DOWN, curses.KEY_UP):
            if self.status['state'] in ['play', 'pause']:
                if not self.seek:
                    self.seek = True
                    self.elapsed, self.total = [int(i) for i in self.status['time'].split(':')]
                if c == curses.KEY_LEFT:
                    self.elapsed = max(self.elapsed - 1, 0)
                elif c == curses.KEY_RIGHT:
                    self.elapsed = min(self.elapsed + 1, self.total)
                elif c == curses.KEY_DOWN:
                    self.elapsed = max(self.elapsed - max(self.total / 100, 1), 0)
                elif c == curses.KEY_UP:
                    self.elapsed = min(self.elapsed + max(self.total / 100, 1), self.total)
                self.status['time'] = '{}:{}'.format(self.elapsed, self.total)
        else:
            if self.seek:
                self.status['time'] = '{}:{}'.format(self.elapsed, self.total)
                if self.status['state'] in ['play', 'pause']:
                    self.mpc.seekid(self.status['songid'], self.elapsed)
                self.seek = False

        # volume control
        if c == ord('9'):
            new_vol = max(int(self.status['volume']) - 1, 0)
            self.mpc.setvol(new_vol)
            self.status['volume'] = str(new_vol)
        elif c == ord('0'):
            new_vol = min(int(self.status['volume']) + 1, 100)
            self.mpc.setvol(new_vol)
            self.status['volume'] = str(new_vol)

        # playback
        elif c == ord(' '):
            self.mpc.pause()
        elif c == ord('s'):
            self.mpc.stop()
        elif c == ord('<'):
            self.mpc.previous()
        elif c == ord('>'):
            self.mpc.next()

        # marks
        elif c == ord('u'):
            self.mpc.consume(1 - int(self.status['consume']))
            self.status['consume'] = 1 - int(self.status['consume'])
        elif c == ord('i'):
            self.mpc.random(1 - int(self.status['random']))
            self.status['random'] = 1 - int(self.status['random'])
        elif c == ord('o'):
            self.mpc.repeat(1 - int(self.status['repeat']))
            self.status['repeat'] = 1 - int(self.status['repeat'])
        elif c == ord('p'):
            self.mpc.single(1 - int(self.status['single']))
            self.status['single'] = 1 - int(self.status['single'])

        # playlist save/load
        elif c == ord('S'):
            name = self.e.getstr('Save')
            try:
                self.mpc.save(name)
            except mpd.CommandError as e:
                self.board['msg'] = str(e).rsplit('} ')[1]
            else:
                self.board['msg'] = 'Playlist {} saved'.format(name)
                self.board['main-playlist'] = 'saved'
        elif c == ord('O'):
            name = self.e.getstr('Load')
            try:
                self.mpc.load(name)
            except mpd.CommandError as e:
                self.board['msg'] = str(e).rsplit('} ')[1]
            else:
                self.board['msg'] = 'Playlist {} loaded'.format(name)

        # basic search
        elif c in [ord('/'), ord('?')]:
            search = self.e.getstr('Find')
            if search:
                self.search = search
                if c == ord('/'):
                    self.search_di = 1
                elif c == ord('?'):
                    self.search_di = -1

        # send to tmod
        self.mdict[self.tmodname].round_one(c)

        # other mods do round_one with no input char
        for modname in self.mdict:
            if modname != self.tmodname:
                self.mdict[modname].round_one(-1)

        # window switch
        # Must be placed AFTER keyevent is dispatched to tmod, 
        # since it happens in OLD tmod. 
        if c in (curses.KEY_F1, curses.KEY_F2, curses.KEY_F3, curses.KEY_F4, curses.KEY_F5, curses.KEY_F6):
            if c == curses.KEY_F1:
                self.tmodname = 'Help'
            elif c == curses.KEY_F2:
                self.tmodname = 'Queue'
            elif c == curses.KEY_F3:
                self.tmodname = 'Database'
            elif c == curses.KEY_F4:
                self.tmodname = 'Lyrics'
            elif c == curses.KEY_F5:
                self.tmodname = 'Artist-Album'
            elif c == curses.KEY_F6:
                self.tmodname = 'Search'
        elif c == ord('h'):
            if self.tmodname != 'Info':
                self.board['prevtmodname'] = self.tmodname
                self.tmodname = 'Info'

    def round_two(self):
        if self.board.has_key('path'):
            self.tmodname = 'Database'

        if self.board.has_key('locate'):
            self.tmodname = 'Queue'

        if self.board.has_key('i_back'):
            self.tmodname = self.board['i_back']

        for mod in self.mlist:
            mod.round_two()

    def uwin(self):
        if ENABLE_OSD:
            self.l.uwin()

        self.mdict[self.tmodname].uwin()

        for mod in self.bmlist:
            mod.uwin()

        curses.doupdate()

    def enter_idle(self):
        '''Enter idle state. Must be called outside idle state.
        
        No return value.'''

        self.mpc.send_idle()
        self.idle = True

    def leave_idle(self):
        '''Leave idle state. Must be called inside idle state.
        
        Return Value: Events received in idle state.'''

        self.mpc.send_noidle()
        self.idle = False

        try:
            return self.mpc.fetch_idle()
        except mpd.PendingCommandError:
            # return None if nothing received
            return None

    def try_enter_idle(self):
        if not self.idle:
            self.enter_idle()

    def try_leave_idle(self):
        if self.idle:
            return self.leave_idle()

    def process(self, fd):
        '''Process init/timeout/mpd/stdin events. Called in main loop.'''
        
        tmod = self.mdict[self.tmodname]
        self.allnsks, self.allpsks = copy.deepcopy(self.nsks), copy.deepcopy(self.psks)
        self.allnsks.extend(tmod.nsks)
        self.allpsks.extend(tmod.psks)

        lastc = self.c

        # get input
        if fd == 'stdin':
            self.c = c = self.stdscr.getch()
        else:
            self.c = c = -1

        # sync vs nosync
        if fd == 'timeout':
            if self.status['state'] == 'play':
                self.sync = True
            elif lastc in self.allnsks:
                self.sync = False
            else:
                self.sync = True
        elif fd == 'init' or fd == 'mpd':
            self.sync = True
        elif fd == 'stdin':
            if c == ord('q'):
                self.loop = False
                return
            elif self.status['state'] == 'play':
                self.sync = True
            elif c in self.allnsks or c in self.allpsks:
                self.sync = False
            else:
                self.sync = True

        self.board.clear()

        if self.sync:
            self.try_leave_idle()

            if c not in self.allpsks and self.pending:
                self.mpc.command_list_ok_begin()
                for task in self.pending:
                    exec('self.mpc.' + task)
                self.mpc.command_list_end()
                self.pending = []

            self.udata()
        self.round_one(c)   # nsks/psks won't cause interaction with server
        self.round_two()    # nsks/psks won't cause interaction with server
        self.uwin()         # won't interact with server

        if fd == 'stdin':
            curses.flushinp()
        else:
            self.try_enter_idle()

    def rdis(self):
        '''Reset display.
        
        Called when SIGWINCH is caught.'''

        curses.endwin()
        self.stdscr.refresh()
        self.height, self.width = self.stdscr.getmaxyx()

        for mod in self.mlist:
            mod.rdis()

    def main_loop(self):
        '''Main loop.'''

        poll = select.poll()
        poll.register(self.mpc.fileno(), select.POLLIN)
        poll.register(0, select.POLLIN)

        # already in idle state since __enter__ calls try_enter_idle in the end.
        self.loop = True
        while self.loop:
            try:
                responses = poll.poll(200)
                if not responses:
                    self.process(fd='timeout')
                else:
                    for fd, event in responses:
                        if fd == self.mpc.fileno() and event & select.POLLIN:
                            self.process(fd='mpd')
                        elif fd == 0 and event & select.POLLIN:
                            self.process(fd='stdin')
            except select.error:
                # SIGWINCH will cause select.error,
                # so no explicit SIGWINCH signal handler is used,
                # and SIGWINCH before poll starts won't be handled

                # reset display
                self.rdis()
                # eat up KEY_RESIZE and update
                self.process(fd='stdin')

if __name__ == '__main__':
    try:
        locale.setlocale(locale.LC_ALL,'')

        if not os.path.isdir(LYRICS_DIR):
            os.makedirs(LYRICS_DIR)

        with MPY() as mpy:
            mpy.main_loop()
    finally:
        curses.endwin()