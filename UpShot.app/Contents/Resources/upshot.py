#!/usr/bin/env python
import glob
import logging
import os
import shutil
import sys
import time
import urllib
import urlparse
import requests
import base64
import json
import subprocess

from AppKit import *
from PyObjCTools import AppHelper

from watchdog.events import (FileCreatedEvent, FileMovedEvent,
                             FileSystemEventHandler)
from watchdog.observers import Observer

# import DropboxDetect
import Preferences
from lib import utils
from lib.notifications import Growler
from lib.windows import alert


SCREENSHOT_DIR = utils.get_pref(
    domain='com.apple.screencapture', key='location',
    default=os.path.join(os.environ['HOME'], 'Desktop'))
# DROPBOX_DIR = utils.detect_dropbox_folder()
PUBLIC_DIR = os.path.join(os.environ['HOME'], 'Public')
SHARE_DIR = os.path.join(PUBLIC_DIR, 'Screenshots')

HOMEPAGE_URL = 'http://github.com/mikeknoop/upshot'
# DROPBOX_PUBLIC_INFO = 'https://www.dropbox.com/help/16'

TIME_THRESHOLD = 15  # How many seconds after creation do we handle a file?

NOTIFICATION_SOUND = os.path.join(os.path.dirname(os.path.realpath(__file__)), "notification.wav")

# Set up logging
LOG_LEVEL = logging.DEBUG
logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger('upshot')

# Local settings
try:
    from settings_local import *
except ImportError:
    pass


class Upshot(NSObject):
    """OS X status bar icon."""
    image_paths = {
        'icon32': 'upshot-bw.png',
        'icon32-off': 'upshot-bw.png',
    }
    images = {}
    statusitem = None
    observer = None  # Screenshot directory observer.
    menuitems = {}  # Shortcut to our menuitems.

    def applicationDidFinishLaunching_(self, notification):
        # if not DROPBOX_DIR:  # Oh-oh.
        #     alert('Unable to detect Dropbox folder',
        #           'UpShot requires Dropbox, for now. Please install it, then '
        #           'try again.', ['OK'])
        #     self.quit_(self)

        # if not os.path.exists(PUBLIC_DIR):  # No public folder?
        #     pressed = alert(
        #         'Unable to detect Public Dropbox folder',
        #         'UpShot requires a Dropbox Public folder. You seem to have '
        #         'Dropbox, but no Public folder.\n\n'
        #         'Since October 2012, Dropbox will only create a public '
        #         'folder for you if you opt in to it.\n\n'
        #         'Please do so before using UpShot.',
        #         ['Learn How to Create a Dropbox Public Folder',
        #          'Quit UpShot'])
        #     if pressed == NSAlertFirstButtonReturn:
        #         # Open Dropboc opt-in
        #         sw = NSWorkspace.sharedWorkspace()
        #         sw.openURL_(NSURL.URLWithString_(DROPBOX_PUBLIC_INFO))
        #     self.quit_(self)

        log.debug('Launching...')
        self.build_menu()

        # Go do something useful.
        log.debug('Starting to listen...')
        self.startListening_()


    def build_menu(self):
        """Build the OS X status bar menu."""
        # Create the statusbar item
        statusbar = NSStatusBar.systemStatusBar()
        self.statusitem = statusbar.statusItemWithLength_(NSVariableStatusItemLength)

        # Set statusbar icon and color/grayscale mode.
        for tag, img in self.image_paths.items():
            self.images[tag] = NSImage.alloc().initByReferencingFile_(img)
            self.images[tag].setTemplate_(True) # force grayscale, False is for color
        self.statusitem.setImage_(self.images['icon32'])

        self.statusitem.setHighlightMode_(1)
        self.statusitem.setToolTip_('Upshot Screenshot Sharing')

        # Build menu.
        self.menu = NSMenu.alloc().init()

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Browse Screenshots', 'openShareDir:', '')
        self.menu.addItem_(m)
        self.menuitems['opensharedir'] = m

        self.menu.addItem_(NSMenuItem.separatorItem())

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Start Screenshot Sharing', 'startListening:', '')
        m.setHidden_(True)  # Sharing is on by default.
        self.menu.addItem_(m)
        self.menuitems['start'] = m

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Pause Screenshot Sharing', 'stopListening:', '')
        self.menu.addItem_(m)
        self.menuitems['stop'] = m

        # m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        #     "Need to detect Dropbox ID. Open Preferences!", '', '')
        # m.setHidden_(True)  # We hopefully don't need this.
        # self.menu.addItem_(m)
        # self.menuitems['needpref'] = m

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Preferences...', 'openPreferences:', '')
        self.menu.addItem_(m)
        self.menuitems['preferences'] = m

        self.menu.addItem_(NSMenuItem.separatorItem())

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Open UpShot Project Website', 'website:', '')
        self.menu.addItem_(m)
        self.menuitems['website'] = m

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'About UpShot', 'about:', '')
        self.menu.addItem_(m)
        self.menuitems['about'] = m

        self.menu.addItem_(NSMenuItem.separatorItem())

        m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            'Quit UpShot', 'quit:', '')
        self.menu.addItem_(m)
        self.menuitems['quit'] = m

        self.statusitem.setMenu_(self.menu)

    def update_menu(self):
        """Update status bar menu based on app status."""
        if self.statusitem is None:
            return

        # Apply iconset
        # self.images['icon32'].setTemplate_(
        #     utils.get_pref('iconset') == 'grayscale')

        running = (self.observer is not None)
        self.statusitem.setImage_(self.images['icon32' if running else
                                              'icon32-off'])

        # if utils.get_pref('dropboxid'):  # Runnable.
        if utils.get_pref('upload_url'):  # Runnable.
            self.menuitems['stop'].setHidden_(not running)
            self.menuitems['start'].setHidden_(running)
            # self.menuitems['needpref'].setHidden_(True)
        else:  # Need settings.
            self.menuitems['start'].setHidden_(True)
            self.menuitems['stop'].setHidden_(True)
            # self.menuitems['needpref'].setHidden_(False)

    def openShareDir_(self, sender=None):
        """Open the share directory in Finder."""
        log.debug('Trying to open share dir in finder: %s' % SHARE_DIR)
        sw = NSWorkspace.sharedWorkspace()
        sw.openFile_(SHARE_DIR)

    def about_(self, sender=None):
        """Open standard About dialog."""
        app = NSApplication.sharedApplication()
        app.activateIgnoringOtherApps_(True)
        app.orderFrontStandardAboutPanel_(sender)

    def website_(self, sender=None):
        """Open the UpShot homepage in a browser."""
        sw = NSWorkspace.sharedWorkspace()
        sw.openURL_(NSURL.URLWithString_(HOMEPAGE_URL))

    def openPreferences_(self, sender=None):
        Preferences.PreferencesWindowController.showWindow()

    def startListening_(self, sender=None):
        """Start listening for changes to the screenshot dir."""
        event_handler = ScreenshotHandler()
        self.observer = Observer()
        self.observer.schedule(event_handler, path=SCREENSHOT_DIR)
        self.observer.start()
        self.update_menu()
        log.debug('Listening for screen shots to be added to: %s' % (
                  SCREENSHOT_DIR))

        # growl = Growler.alloc().init()
        # growl.notify('UpShot started.')

    def stopListening_(self, sender=None):
        """Stop listening to changes ot the screenshot dir."""
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            log.debug('Stop listening for screenshots.')

            # growl = Growler.alloc().init()
            # growl.notify('UpShot paused.')
        self.update_menu()

    def restart_(self, sender=None):
        self.stopListening_()
        self.startListening_()

    def quit_(self, sender=None):
        """Default quit event."""
        log.debug('Terminating.')
        self.stopListening_()
        NSApplication.sharedApplication().terminate_(sender)


class ScreenshotHandler(FileSystemEventHandler):
    """Handle file creation events in our screenshot dir."""
    def on_created(self, event):
        """File creation, handles screen clips."""
        log.debug('Screen clip candidate...')
        f = event.src_path
        if isinstance(event, FileCreatedEvent) and not (
            os.path.basename(f).startswith('.')):
            self.handle_screenshot_candidate(f)

    def on_moved(self, event):
        """
        Catch move event: For full screenshots, OS X creates a temp file,
        then moves it to its final name.
        """
        log.debug('Full screenshot candidate...')
        if not isinstance(event, FileMovedEvent):
            return
        self.handle_screenshot_candidate(event.dest_path)

    def handle_screenshot_candidate(self, f):
        """Given a candidate file, handle it if it's a screenshot."""
        # Do not act on files that are too old (so we don't swallow old files
        # that are not new screenshots).
        log.debug('Handling the screenshot candidate...')

        # sleep for a bit to let the file system catch up
        # else `is_screenshot` might be wrong
        time.sleep(0.5)

        if os.path.getctime(f) - time.time() > TIME_THRESHOLD:
            log.debug('Time threshhold exceeded.')
            return

        # The file could be anything. Only act if it's a screenshot.
        if not utils.is_screenshot(f):
            log.debug('Does not look like a screenshot.')
            return

        # Create target dir if needed.
        if not os.path.isdir(SHARE_DIR):
            log.debug('Creating share dir %s' % SHARE_DIR)
            os.makedirs(SHARE_DIR)

        # Determine target filename in share directory.
        log.debug('Moving %s to %s' % (f, SHARE_DIR))
        if utils.get_pref('randomize'):  # Randomize file names?
            ext = os.path.splitext(f)[1]
            while True:
                shared_name = utils.randname() + ext
                target_file = os.path.join(SHARE_DIR, shared_name)
                if not os.path.exists(target_file):
                    log.debug('New file name is: %s' % shared_name)
                    break
        else:
            shared_name = os.path.basename(f)
            target_file = os.path.join(SHARE_DIR, shared_name)

        # We repurpose the "copyonly" setting.
        # True: Move screenshot to SHARE_DIR and leave it there
        # False: Remove all screenshots from the filesystem after upload

        # Move/copy file there.
        if (utils.get_pref('retinascale') and
            utils.resampleRetinaImage(f, target_file)):
            os.unlink(f)
        else:
            shutil.move(f, target_file)

        try:
            # Actually upload the file to our custom url
            if not utils.get_pref('upload_url'):
                log.debug('No Upload URL defined: {0}'.format(utils.get_pref('upload_url')))
                raise Exception('No Upload URL defined.')

            content = base64.b64encode(open(target_file).read())
            payload = {'image': content}
            headers = {'Content-Type': 'application/json'}
            r = requests.post(utils.get_pref('upload_url'), data=json.dumps(payload), headers=headers)

            log.debug('Response raw content:')
            log.debug(r.content)

            url = r.json()['link']
            log.debug('Share URL is %s' % url)

            log.debug('Copying to clipboard.')
            utils.pbcopy(url)

            # Remove old file
            if not utils.get_pref('copyonly'):
                os.unlink(target_file)

            # Notify user of success
            if utils.get_pref('notification_growl'):
                growl = Growler.alloc().init()
                growl.setCallback(self.notify_callback)
                growl.notify('Success! Screenshot uploaded.',
                             '%s\n\n' % url,
                             context=target_file)

            if utils.get_pref('notification_sound'):
                return_code = subprocess.call(["afplay", NOTIFICATION_SOUND])
                log.debug('Playing notification sound: {0}'.format(return_code))

        except Exception, e:
            # Notify user of error
            log.debug('EXCEPTION %s' % str(e))
            message = str(e)
            growl = Growler.alloc().init()
            growl.setCallback(self.notify_callback)
            growl.notify('Error uploading screenshot!', message,
                         context=target_file)


    def notify_callback(self, filepath):
        """
        When growl notification is clicked, open Finder with shared file.
        """
        ws = NSWorkspace.sharedWorkspace()
        ws.activateFileViewerSelectingURLs_(
            NSArray.arrayWithObject_(NSURL.fileURLWithPath_(filepath)))


if __name__ == '__main__':
    # Prepare preferences service.
    Preferences.set_defaults()

    app = NSApplication.sharedApplication()
    delegate = Upshot.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
