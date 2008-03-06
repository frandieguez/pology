# -*- coding: UTF-8 -*-

"""
Find messages by regular expression matching.

Matches the regular expression against requested elements of the message,
and reports the message if the match exists. Every matched message is
reported to standard output, with the name of the file from which it comes,
and referent line and entry number within the file.

Sieve options for matching:
  - C{msgctxt:<regex>}: regular expression to match against the C{msgctxt}
  - C{msgid:<regex>}: regular expression to match against the C{msgid}
  - C{msgstr:<regex>}: regular expression to match against the C{msgstr}

If more than one of the matching options are given (e.g. both C{msgid} and
C{msgstr}), the message matches only if all of them match. In case of plural
messages, C{msgid} is considered matched if either C{msgid} or C{msgid_plural}
fields match, and C{msgstr} if any of the C{msgstr} fields match.

Other sieve options:
  - C{accel:<char>}: strip this character as an accelerator marker
  - C{case}: case-sensitive match (insensitive by default)

If accelerator character is not given by C{accel} option, the sieve will try
to guess the accelerator; it may choose wrongly or decide that there are no
accelerators. E.g. an C{X-Accelerator-Marker} header field is checked for the
accelerator character.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

import sys, os, re
from pology.misc.report import error, report_msg_content


class Sieve (object):

    def __init__ (self, options, global_options):

        self.nmatch = 0

        self.accel_explicit = False
        self.accel = ""
        if "accel" in options:
            options.accept("accel")
            self.accel = options["accel"]
            self.accel_explicit = True

        self.rxflags = re.U
        if "case" in options:
            options.accept("case")
        else:
            self.rxflags |= re.I

        self.fields = []
        self.regexs = []
        for field in ("msgctxt", "msgid", "msgstr"):
            if field in options:
                options.accept(field)
                self.fields.append(field)
                self.regexs.append(re.compile(options[field], self.rxflags))

        if not self.fields:
            error("no search pattern given")

        # Indicators to the caller:
        self.caller_sync = False # no need to sync catalogs
        self.caller_monitored = False # no need for monitored messages


    def process_header (self, hdr, cat):

        # Check if the catalog itself states the accelerator character,
        # unless specified explicitly by the command line.
        if not self.accel_explicit:
            accel = cat.possible_accelerator()
            if accel:
                self.accel = accel
            else:
                self.accel = ""


    def process (self, msg, cat):

        if msg.obsolete:
            return

        match = True

        for field, regex in zip(self.fields, self.regexs):

            if field == "msgctxt":
                texts = [msg.msgctxt]
            elif field == "msgid":
                texts = [msg.msgid, msg.msgid_plural]
            elif field == "msgstr":
                texts = msg.msgstr
            else:
                error("unknown search field '%s'" % field)

            local_match = False

            for text in texts:
                # Remove accelerator.
                if self.accel:
                    text = text.replace(self.accel, "")

                # Check for local match (local match is OR).
                if regex.search(text):
                    local_match = True
                    break

            # Check for global match (global match is AND).
            if not local_match:
                match = False
                break

        if match:
            self.nmatch += 1
            delim = "--------------------"
            if self.nmatch == 1:
                print delim
            report_msg_content(msg, cat, delim=delim, highlight=regex)


    def finalize (self):

        if self.nmatch:
            print "Total matching: %d" % (self.nmatch,)

