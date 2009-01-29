# -*- coding: UTF-8 -*-

"""
Convert delimitor-embedded context to Gettext context.

Context is embedded into C{msgid} field, as the initial part of the field
starting and ending with predefined substrings, the "head" and the "tail".
For example, in::

    msgid ""
    "_:this-is-context\n"
    "This is original text"
    msgstr "This is translated text"

the head is underscore-colon (C{_:}), and the tail newline (C{\n}).

Sieve options:
  - C{head} (mandatory): head string of the delimited context
  - C{tail} (mandatory): tail string of the delimited context
  - C{sync}: do request syncing to disk of modified catalogs

Parameter C{nosync} tells the sieve not to issue request to sync the catalogs,
which means that the files on disk will not be modified unless another sieve in
the chain requests syncing. In this way, the sieve can be used to normalize
contexts for other non-syncing sieves, like L{stats<sieve.stats>} or
L{find-messages<sieve.find_messages>}.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

from pology.sieve import SieveError
from pology.misc.msgreport import warning_on_msg
from pology.misc.escape import unescape


def setup_sieve (p):

    p.set_desc(
    "Convert delimitor-embedded context to Gettext context."
    )

    p.add_param("head", unicode, mandatory=True,
                metavar="STRING",
                desc=
    "Start of the msgid field which indicates that the context follows."
    )
    p.add_param("tail", unicode, mandatory=True,
                metavar="STRING",
                desc=
    "End of context in msgid field, after which the text follows."
    )
    p.add_param("nosync", bool, defval=False,
                desc=
    "Do not request modified catalog to be synced to disk."
    )


class Sieve (object):

    def __init__ (self, params):

        self.nconv = 0

        self.chead = unescape(params.head)
        if not self.chead:
            raise SieveError("context head cannot be empty string")
        self.ctail = unescape(params.tail)
        if not self.ctail:
            raise SieveError("context tail cannot be empty string")

        if params.nosync:
            self.caller_sync = False
            self.caller_monitored = False


    def process (self, msg, cat):

        # Skip messages already having Gettext context.
        if msg.msgctxt or msg.msgctxt_previous:
            return

        msrc = (cat.filename, msg.refline, msg.refentry)

        if msg.msgid.startswith(self.chead):
            pos = msg.msgid.find(self.ctail)
            if pos < 0:
                warning_on_msg("malformed embedded context", msg, cat)
                return

            ctxt = msg.msgid[len(self.chead):pos]
            text = msg.msgid[pos + len(self.ctail):]

            if not ctxt or not text:
                warning_on_msg("empty context or text", msg, cat)
                return

            msg.msgctxt = ctxt
            msg.msgid = text

            self.nconv += 1


    def finalize (self):

        if self.nconv > 0:
            print "Total contexts converted: %d" % (self.nconv,)

