# -*- coding: UTF-8 -*-

"""
Collection of PO entries.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

import copy
import difflib
import os
import re
import tempfile
import time
import types

from pology import PologyError, _, n_
from pology.header import Header, format_datetime
from pology.message import Message as MessageMonitored
from pology.message import MessageUnsafe as MessageUnsafe
from pology.escape import escape_c as escape
from pology.escape import unescape_c as unescape
from pology.fsops import mkdirpath
from pology.monitored import Monitored
from pology.resolve import expand_vars
from pology.wrap import select_field_wrapper


class CatalogSyntaxError (PologyError):
    """
    Exception for errors in catalog syntax.

    This exception is normally raised when parsing a catalog,
    e.g. on invalid syntax or non-decodable characters.
    """

    pass


def _parse_quoted (s):

    sp = s[s.index("\"") + 1:s.rindex("\"")]
    sp = unescape(sp);
    return sp


class _MessageDict:

    def __init__ (self, lcache=True):

        self.manual_comment = []
        self.auto_comment = []
        self.source = []
        self.flag = []
        self.obsolete = False
        self.msgctxt_previous = []
        self.msgid_previous = []
        self.msgid_plural_previous = []
        self.msgctxt = []
        self.msgid = []
        self.msgid_plural = []
        self.msgstr = []
        self.refline = -1
        self.refentry = -1

        if lcache:
            self._lines_all = []
            self._lines_manual_comment = []
            self._lines_auto_comment = []
            self._lines_source = []
            self._lines_flag = []
            self._lines_msgctxt_previous = []
            self._lines_msgid_previous = []
            self._lines_msgid_plural_previous = []
            self._lines_msgctxt = []
            self._lines_msgid = []
            self._lines_msgid_plural = []
            self._lines_msgstr = []


def _read_lines_and_encoding (file, filename):

    fstr = file.read()
    # Determine line ending.
    maxlno = 0
    for clend in (b"\r\n", b"\n", b"\r"): # "\r\n" should be checked first
        lno = len(fstr.split(clend))
        if maxlno < lno:
            maxlno = lno
            lend = clend
    lines = [x + b"\n" for x in fstr.split(lend)]
    if lines[-1] == b"\n":
        lines.pop()

    enc = None
    enc_rx = re.compile(rb"Content-Type:.*charset=(.+?)\\n", re.I)
    for line in lines:
        if line.strip().startswith(b"#:"):
            break
        m = enc_rx.search(line)
        if m:
            enc = m.group(1).strip()
            if not enc or enc == b"CHARSET": # no encoding given
                enc = None
            break
    if enc is None:
        enc = b"UTF-8" # fall back to UTF-8 if encoding not found
    enc = enc.decode()

    enclines = []
    lno = 0
    for line in lines:
        lno += 1
        try:
            encline = line.decode(enc)
        except UnicodeDecodeError as e:
            raise CatalogSyntaxError(
                _("@info",
                  "Text decoding failure at %(file)s:%(line)d:%(col)d "
                  "under assumed encoding '%(enc)s'.",
                  file=filename, line=lno, col=e.start, enc=enc))
        enclines.append(encline)

    return enclines, enc


def _parse_po_file (file, MessageType=MessageMonitored,
                    headonly=False, lcache=True):

    if isinstance(file, str):
        filename = file
        file = open(filename, "rb")
        close_later = True
    else:
        if hasattr(file, "name"):
            filename = file.name
        else:
            filename = _("@item generic name for the source or destination "
                         "of data being read or written",
                         "&lt;stream&gt;").resolve("none")
        close_later = False
    lines, fenc = _read_lines_and_encoding(file, filename)
    if close_later:
        file.close()

    ctx_modern, ctx_obsolete, \
    ctx_previous, ctx_current, \
    ctx_none, ctx_msgctxt, ctx_msgid, ctx_msgid_plural, ctx_msgstr = list(range(9))

    messages1 = list()
    lno = 0
    eno = 0

    class Namespace: pass
    loc = Namespace()
    loc.lno = 0
    loc.tail = None
    loc.msg = _MessageDict(lcache)
    loc.life_context = ctx_modern
    loc.field_context = ctx_none
    loc.age_context = ctx_current

    # The message has been completed by the previous line if the context just
    # switched away from ctx_msgstr;
    # call whenever context switch happens, *before* assigning new context.
    nlines = len(lines)
    def try_finish ():
        if loc.field_context == ctx_msgstr:
            messages1.append(loc.msg)
            loc.msg = _MessageDict(lcache)
            loc.field_context = ctx_none
            # In header-only mode, the first message read is the header.
            # Compose the tail of this and rest of the lines, and
            # set lno to nlines for exit.
            if headonly:
                # If not at end of file, current line is part of
                # first message and should be retained in the tail.
                offset = loc.lno < nlines and 1 or 0
                loc.tail = "".join(lines[loc.lno - offset:])
                loc.lno = nlines

    while loc.lno < nlines: # sentry for last entry
        line_raw = lines[lno]
        loc.lno += 1
        lno = loc.lno # shortcut
        line = line_raw.strip()
        if not line:
            continue

        string_follows = True
        loc.life_context = ctx_modern
        loc.age_context = ctx_current

        if line.startswith("#"):

            if 0: pass

            elif line.startswith("#~|"):
                line = line[3:].lstrip()
                loc.age_context = ctx_previous

            elif line.startswith("#~"):
                line = line[2:].lstrip()
                loc.life_context = ctx_obsolete

            elif line.startswith("#|"):
                line = line[2:].lstrip()
                loc.age_context = ctx_previous

            elif line.startswith("#:"):
                try_finish()
                string_follows = False
                for srcref in line[2:].split(" "):
                    srcref = srcref.strip()
                    if srcref:
                        lst = srcref.split(":", 1)
                        if len(lst) == 2:
                            file = lst[0]
                            try:
                                line = int(lst[1])
                                assert line > 0
                            except:
                                file = srcref
                                line = -1
                            loc.msg.source.append((file, line))
                        else:
                            loc.msg.source.append((srcref, -1))

            elif line.startswith("#,"):
                try_finish()
                string_follows = False
                for flag in line[2:].split(","):
                    flag = flag.strip()
                    if flag:
                        loc.msg.flag.append(flag)

            elif line.startswith("#."):
                try_finish()
                string_follows = False
                loc.msg.auto_comment.append(line[2:].lstrip())

            elif line.startswith("#"):
                try_finish()
                string_follows = False
                loc.msg.manual_comment.append(line[2:].lstrip())

            else:
                # Cannot reach, all unknown comments treated as manual above.
                raise CatalogSyntaxError(
                    _("@info",
                      "Unknown comment type at %(file)s:%(line)d.",
                      file=filename, line=lno))

        if line and string_follows: # for starting fields
            if 0: pass

            elif line.startswith("msgctxt"):
                # TODO: Assert context.
                try_finish()
                loc.field_context = ctx_msgctxt
                line = line[7:].lstrip()

            elif line.startswith("msgid_plural"):
                # TODO: Assert context.
                # No need for try_finish(), msgid_plural cannot start message.
                loc.field_context = ctx_msgid_plural
                line = line[12:].lstrip()

            elif line.startswith("msgid"):
                # TODO: Assert context.
                try_finish()
                if loc.life_context == ctx_obsolete:
                    loc.msg.obsolete = True
                loc.field_context = ctx_msgid
                if loc.age_context == ctx_current:
                    loc.msg.refline = lno
                    loc.msg.refentry = eno
                    eno += 1
                line = line[5:].lstrip()

            elif line.startswith("msgstr"):
                # TODO: Assert context.
                loc.field_context = ctx_msgstr
                line = line[6:].lstrip()
                msgstr_i = 0
                if line.startswith("["):
                    line = line[1:].lstrip()
                    llen = len(line)
                    p = 0
                    while p < llen and line[p].isdigit():
                        p += 1
                    if p == 0:
                        raise CatalogSyntaxError(
                            _("@info",
                              "Malformed '%(field)s' ordinal "
                              "at %(file)s:%(line)d.",
                              file=filename, line=lno, field="msgstr"))
                    msgstr_i = int(line[:p])
                    line = line[p:].lstrip()
                    if line.startswith("]"):
                        line = line[1:].lstrip()
                    else:
                        raise CatalogSyntaxError(
                            _("@info",
                              "Malformed '%(field)s' ordinal "
                              "at %(file)s:%(line)d.",
                              file=filename, line=lno, field="msgstr"))
                # Add missing msgstr entries.
                for i in range(len(loc.msg.msgstr), msgstr_i + 1):
                    loc.msg.msgstr.append([])

            elif not line.startswith("\""):
                raise CatalogSyntaxError(
                    _("@info",
                      "Unknown field name at %(file)s:%(line)d.",
                      file=filename, line=lno))

        if line and string_follows: # for continuing fields
            if line.startswith("\""):
                s = _parse_quoted(line)
                if loc.age_context == ctx_previous:
                    if loc.field_context == ctx_msgctxt:
                        loc.msg.msgctxt_previous.append(s)
                    elif loc.field_context == ctx_msgid:
                        loc.msg.msgid_previous.append(s)
                    elif loc.field_context == ctx_msgid_plural:
                        loc.msg.msgid_plural_previous.append(s)
                else:
                    if loc.field_context == ctx_msgctxt:
                        loc.msg.msgctxt.append(s)
                    elif loc.field_context == ctx_msgid:
                        loc.msg.msgid.append(s)
                    elif loc.field_context == ctx_msgid_plural:
                        loc.msg.msgid_plural.append(s)
                    elif loc.field_context == ctx_msgstr:
                        loc.msg.msgstr[msgstr_i].append(s)
            else:
                raise CatalogSyntaxError(
                    _("@info",
                      "Expected string continuation at %(file)s:%(line)d.",
                      file=filename, line=lno))

        # Update line caches.
        if lcache:
            loc.msg._lines_all.append(line_raw)
            if 0: pass
            elif line_raw.startswith("#:"):
                loc.msg._lines_source.append(line_raw)
            elif line_raw.startswith("#,"):
                loc.msg._lines_flag.append(line_raw)
            elif line_raw.startswith("#."):
                loc.msg._lines_auto_comment.append(line_raw)
            elif line_raw.startswith("#") and line_raw[1:2] not in ("~", "|"):
                loc.msg._lines_manual_comment.append(line_raw)
            elif loc.age_context == ctx_previous:
                if loc.field_context == ctx_msgctxt:
                    loc.msg._lines_msgctxt_previous.append(line_raw)
                elif loc.field_context == ctx_msgid:
                    loc.msg._lines_msgid_previous.append(line_raw)
                elif loc.field_context == ctx_msgid_plural:
                    loc.msg._lines_msgid_plural_previous.append(line_raw)
                else:
                    raise PologyError(
                        _("@info",
                          "Internal problem (%(id)d) at %(file)s:%(line)d.",
                          id=11, file=filename, line=lno))
            elif loc.age_context == ctx_current:
                if loc.field_context == ctx_msgctxt:
                    loc.msg._lines_msgctxt.append(line_raw)
                elif loc.field_context == ctx_msgid:
                    loc.msg._lines_msgid.append(line_raw)
                elif loc.field_context == ctx_msgid_plural:
                    loc.msg._lines_msgid_plural.append(line_raw)
                elif loc.field_context == ctx_msgstr:
                    loc.msg._lines_msgstr.append(line_raw)
                else:
                    raise PologyError(
                        _("@info",
                          "Internal problem (%(id)d) at %(file)s:%(line)d.",
                          id=12, file=filename, line=lno))
            else:
                raise PologyError(
                    _("@info",
                      "Internal problem (%(id)d) at %(file)s:%(line)d.",
                      id=10, file=filename, line=lno))

    try_finish() # the last message

    if len(messages1) == 0:
        raise CatalogSyntaxError(
            _("@info",
              "No header at %(file)s:%(line)d.",
              file=filename, line=lno))

    # Join fields.
    join_or_none = lambda x: "".join(x) if x else None
    for i, msg in enumerate(messages1):
        msg.msgctxt_previous = join_or_none(msg.msgctxt_previous)
        msg.msgid_previous = join_or_none(msg.msgid_previous)
        msg.msgid_plural_previous = join_or_none(msg.msgid_plural_previous)
        msg.msgctxt = join_or_none(msg.msgctxt)
        msg.msgid = join_or_none(msg.msgid)
        msg.msgid_plural = join_or_none(msg.msgid_plural)
        msg.msgstr = [join_or_none(x) for x in msg.msgstr]
        if i > 0 and msg.msgid == "" and msg.msgctxt is None:
            raise CatalogSyntaxError(
                _("@info",
                  "Empty message at %(file)s:%(line)d.",
                  file=filename, line=msg.refline))

    # Repack raw dictionaries as message objects.
    messages2 = []
    for msg1 in messages1:
        messages2.append(MessageType(msg1.__dict__))

    return (messages2, fenc, loc.tail)


def _srcref_repack (srcrefs):
    srcdict = {}
    for file, line in srcrefs:
        if not file in srcdict:
            srcdict[file] = [line]
        else:
            srcdict[file].append(line)
    srcdict[file].sort()
    return srcdict


_Catalog_spec = {
    # Data.
    "header" : {"type" : Header},
    "filename" : {"type" : (str,)},
    "name" : {"type" : (str,), "derived" : True},
    "*" : {}, # messages sequence: the type is assigned at construction
}


class Catalog (Monitored):
    """
    Class for access and operations on PO catalogs.

    Catalog behaves as an ordered sequence of messages. The typical way of
    iterating over the messages from a PO file on disk would be::

        cat = Catalog("relative/path/foo.po")
        for msg in cat:
            ...
            (do something with msg)
            ...
        cat.sync()

    where L{sync()<sync>} method is used to write any modifications back to
    the disk.

    The header entry of the catalog is not part of the message sequence,
    but is provided by the L{header} attribute, an object of
    type different from an ordinary message entry.

    The catalog is a I{monitored} class.
    Catalog message entries themeselves may also be monitored (default),
    but need not, depending on the mode of creation.

    @ivar header: the header entry
    @type header: L{Header}

    @ivar filename: the file name which the catalog was created with
    @type filename: string

    @ivar name: (read-only)
        the name of the catalog

        Determined as base of the filename, without extension.
    @type name: string

    @see: L{Monitored}
    @see: L{Message}, L{MessageUnsafe}
    @see: L{Header}
    """

    def __init__ (self, filename,
                  create=False, truncate=False,
                  wrapping=None, monitored=True,
                  headonly=False, readfh=None, single_entry=0):
        """
        Build a message catalog by reading from a PO file or creating anew.

        The message entries in the catalog may be monitored themselves or not.
        That is, when monitoring is requested, entries are represented by
        the L{Message} class, otherwise with L{MessageUnsafe}.

        Monitored messages are usually appropriate when the application is
        expected to modify them. Non-monitored messages should provide better
        performance, so use them whenever the catalog is opened for read-only
        purposes (such as checks).

        Catalog can also be opened in header-only mode, for better
        performance when only the header data is needed. This mode provides
        L{header} attribute as usual, but the rest of entries are
        unavailable. If any of the operations dealing with message entries
        are invoked, an error is signaled.

        Instead of opening and reading from catalog's filename,
        catalog can be read from a file-like object provided by
        C{readfh} parameter.
        Same as when reading from file on disk, text will be decoded
        using catalog's encoding after reading it from C{readfh}.

        If a problem which prevents construction of a valid catalog is
        detected while parsing a PO file, L{CatalogSyntaxError} is raised.

        @param filename: name of the PO catalog on disk, or new catalog
        @type filename: string

        @param create:
            whether a blank catalog can be created when the PO file does
            not already exist, or signal an error
        @type create: bool

        @param truncate:
            whether catalog should be empty (and with uninitialized header)
            regardless of whether it is opened or created
        @type truncate: bool

        @param wrapping:
            sequence of keywords specifying wrapping policy for
            message text fields (C{msgid}, C{msgstr}, etc.).
            See L{select_field_wrapper<wrap.select_field_wrapper>}
            function for possible keywords and their effects on wrapping.
            If given as C{None}, it will be deduced from the catalog
            (see L{wrapping} method).
        @type wrapping: sequence of strings

        @param monitored: whether the message entries are monitored
        @type monitored: bool

        @param headonly: whether to open in header-only mode
        @type headonly: bool

        @param readfh: file to read the catalog from
        @type readfh: file-like object
        """

        self._monitored = monitored

        # Select type of message object to use.
        if monitored:
            message_type = MessageMonitored
        else:
            message_type = MessageUnsafe

        # Signal if catalog should exist on disk but does not.
        if not create and not (os.path.exists(filename) or readfh):
            raise PologyError(
                _("@info",
                  "File '%(file)s' does not exist.",
                  file=filename))

        # Read messages or create empty catalog.
        if not truncate and (os.path.exists(filename) or readfh):
            file = readfh or filename
            m, e, t = _parse_po_file(file, message_type, headonly, monitored)
            self._encoding = e
            self._created_from_scratch = False
            if not m[0].msgctxt and not m[0].msgid:
                # Proper PO, containing the header.
                self._header = Header(m[0])
                self._header._committed = True # status for sync
                if (single_entry > 0):
                    self.__dict__["*"] = [m[single_entry]]
                else:
                    self.__dict__["*"] = m[1:]
            else:
                # Improper PO, missing the header.
                self._header = Header()
                self._header._committed = False # status for sync
                if (single_entry > 0):
                    self.__dict__["*"] = [m[single_entry-1]]
                else:
                    self.__dict__["*"] = m
            self._tail = t
        else:
            self._encoding = "UTF-8"
            self._created_from_scratch = True
            self._header = Header()
            self._header._committed = False # status for sync
            self.__dict__["*"] = []
            self._tail = None

        self._filename = filename

        self._messages = self.__dict__["*"] # nicer name for the sequence

        # Fill in the message key-position links.
        # Set committed and remove-on-sync status.
        self._msgpos = {}
        for i in range(len(self._messages)):
            self._msgpos[self._messages[i].key] = i
            self._messages[i]._committed = True
            self._messages[i]._remove_on_sync = False

        # Initialize monitoring.
        final_spec = copy.deepcopy(_Catalog_spec)
        final_spec["*"]["type"] = message_type
        self.assert_spec_init(final_spec)

        # Inverse map (by msgstr) will be computed on first use.
        self._invmap = None

        # Cached plural definition from the header.
        self._plustr = ""

        # Cached language of the translation.
        # None means the language has not been determined.
        self._lang = None
        self._lang_determined = False

        # Cached environments.
        self._envs = None
        self._envs_determined = False

        # Cached accelerator markers.
        self._accels = None
        self._accels_determined = False

        # Cached markup types.
        self._mtypes = None
        self._mtypes_determined = False

        # Cached wrapping policy.
        if wrapping is None:
            self._wrap_determined = False
            self._wrapf = None
            self._wrapkw = None
        else:
            self._wrap_determined = True
            self._wrapf = select_field_wrapper(wrapping)
            self._wrapkw = tuple(wrapping)


    def _assert_headonly (self):

        if self._tail:
            raise PologyError(
                _("@info",
                  "Trying to access catalog messages in header-only mode."))


    def __getattr__ (self, att):
        """
        Attribute getter.

        Processes read-only attributes, and sends others to the base class.

        @param att: name of the attribute to get
        @returns: attribute value
        """
        if 0: pass

        elif att == "name":
            basename = os.path.basename(self._filename)
            p = basename.rfind(".")
            if p >= 0:
                return basename[:p]
            else:
                return basename

        else:
            return Monitored.__getattr__(self, att)


    def __len__ (self):
        """
        The number of messages in the catalog.

        The number includes obsolete entries, and excludes header entry.

        @returns: the number of messages
        @rtype: int
        """

        self._assert_headonly()
        return len(self._messages)


    def __getitem__ (self, ident):
        """
        Get message by position or another message.

        If the position is out of range, or the lookup message does not have
        a counterpart in this catalog with the same key, an error is signaled.

        Runtime complexity O(1), regardless of the C{ident} type.

        @param ident: position index or another message
        @type ident: int or L{Message_base}

        @returns: reference to the message in catalog
        @rtype: L{Message_base}
        """

        self._assert_headonly()
        self.assert_spec_getitem()
        if not isinstance(ident, int):
            ident = self._msgpos[ident.key]
        return self._messages[ident]


    def __setitem__ (self, ident, msg):
        """
        Set message by position or another message.

        If the position is out of range, or the lookup message does not have
        a counterpart in this catalog with the same key, an error is signaled.

        Runtime complexity O(1), regardless of the C{ident} type.

        @param ident: position index or another message
        @type ident: int or L{Message_base}

        @returns: reference to the message in catalog
        @rtype: L{Message_base}
        """

        self._assert_headonly()
        self.assert_spec_setitem(msg)
        if not isinstance(ident, int):
            ident = self._msgpos[ident.key]
        self._messages[ident] = msg
        if self._messages[ident] is not msg:
            self.__dict__["#"]["*"] += 1
        return self._messages[ident]


    def __contains__ (self, msg):
        """
        Whether the message with the same key exists in the catalog.

        Runtime complexity O(1).

        @param msg: message to look for
        @type msg: L{Message_base}

        @returns: C{True} if the message exists
        @rtype: bool
        """

        self._assert_headonly()
        return msg.key in self._msgpos


    def __eq__ (self, ocat):
        """
        Whether two catalogs are equal in all apparent parts.

        Catalogs are considered equal if they are of the same length,
        their headers are equal, and each two messages with the
        same position are equal.

        Runtime complexity O(n).

        @returns: C{True} if catalogs are equal
        @rtype: bool
        """

        if len(self) != len(ocat):
            return False
        if self.header != ocat.header:
            return False
        for i in range(len(ocat)):
            if self[i] != ocat[i]:
                return False
        return True


    def __ne__ (self, ocat):
        """
        Whether two catalogs are equal in all apparent parts.

        Equivalent to C{not (self == ocat)}.

        @returns: C{False} if catalogs are equal
        @rtype: bool
        """

        return not self.__eq__(ocat)


    def find (self, msg, wobs=True):
        """
        Position of the message in the catalog.

        Runtime complexity O(1).

        @param msg: message to look for
        @type msg: L{Message_base}
        @param wobs: obsolete messages considered non-existant if C{False}
        @type wobs: bool

        @returns: position index if the message exists, -1 otherwise
        @rtype: int
        """

        self._assert_headonly()
        if msg.key in self._msgpos:
            if wobs or not msg.obsolete:
                return self._msgpos[msg.key]
        return -1


    def get (self, msg, defmsg=None):
        """
        Get message by key of another message, with default fallback.

        If the lookup message C{msg} does not have a counterpart
        in this catalog with the same key, C{defmsg} is returned.
        C{msg} can also be C{None}, when C{defmsg} is returned.

        Runtime complexity O(1).

        @param msg: message for the lookup by key
        @type msg: L{Message_base} or None
        @param defmsg: fallback in case lookup failed
        @type defmsg: any

        @returns: reference to the message in catalog, or default
        @rtype: L{Message_base} or type(defmsg)
        """

        if msg is None:
            return defmsg
        pos = self.find(msg)
        if pos >= 0:
            return self._messages[pos]
        else:
            return defmsg


    def add (self, msg, pos=None, srefsyn={}):
        """
        Add a message to the catalog.

        If the message with the same key already exists in the catalog,
        it will be replaced with the new message, ignoring position.
        The return value will be C{None}.

        If the message does not exist in the catalog, when the position is
        C{None}, the insertion will be attempted such as that the messages be
        near according to the source references; if the position is not
        C{None}, the message is inserted at the given position.
        The return value will be the true insertion position.

        Negative position can be given as well. It counts backward from
        the first non-obsolete message if the message to be added
        is not obsolete, or from last message otherwise.

        When the message is inserted according to source references,
        a dictionary of file paths to consider synonymous can be given
        by the C{srefsyn}. The key is the file path for which the synonyms
        are being given, and the value the list of synonymous file paths.
        The mapping is not symmetric; if B is in the list of synonyms to A,
        A will not be automatically considered to be among synonyms of B,
        unless explicitly given in the list of synonyms to B.

        Runtime complexity O(1) if the message is present in the catalog;
        O(n - pos) if the position is given and the message is not present;
        O(n) if the position is not given and the message is not present.

        @param msg: message to insert
        @type msg: L{Message_base}

        @param pos: position index to insert at
        @type pos: int or None

        @param srefsyn: synonymous names to some of the source files
        @type srefsyn: {string: [string*]*}

        @returns: if inserted, the position where inserted
        @rtype: int or None
        """

        return self.add_more([(msg, pos)], srefsyn=srefsyn)[0]


    def add_more (self, msgpos, cumulative=False, srefsyn={}):
        """
        Add more than one message to the catalog.

        Like L{add}, except that several messages are added in one call.
        This significantly speeds up insertion when insertion positions of
        all messages are known beforehand.

        Insertion positions can be given relative to state before the call,
        or cumulative to earlier insertions in the list.
        For example, if insertions are given as C{[(msg1, 2), (msg2, 5)]} and
        not cumulative, then the resulting position for C{msg1} will be 2,
        and for C{msg2} 6 (assuming that both messages actually got inserted).
        This behavior can be toggled by the C{cumulative} parameter.

        @param msgpos: messages with target insertion positions
        @type msgpos: [(L{Message_base}, int), ...]
        @param cumulative: whether input positions are cumulative
        @type cumulative: bool
        @param srefsyn: synonymous names to some of the source files
        @type srefsyn: {string: [string*]*}

        @returns: positions where inserted, or None where replaced
        @rtype: [int or None, ...]
        """

        self._assert_headonly()
        for msg, pos in msgpos:
            self.assert_spec_setitem(msg)
            if not msg.msgid and msg.msgctxt is None:
                raise PologyError(
                    _("@info",
                      "Trying to insert message with empty key into catalog."))

        # Resolve backward positions, set aside automatic positions,
        # set aside replacements.
        msgpos_ins = []
        msgs_auto = []
        msgs_repl = []
        for msg, pos in msgpos:
            if msg.key not in self._msgpos:
                if pos is not None:
                    if pos < 0:
                        pos = len(self._messages) + pos
                    if pos < 0 or pos > len(self._messages):
                        raise PologyError(
                            _("@info",
                              "Trying to insert message into catalog by "
                              "position out of range."))
                    msgpos_ins.append((msg, pos))
                else:
                    msgs_auto.append(msg)
            else:
                msgs_repl.append(msg)

        # Sort messages to be inserted by resolved positions.
        msgpos_ins = sorted(msgpos_ins, key=lambda x: x[1])

        # Resolve messages to be inserted by automatic positions.
        for msg in msgs_auto:
            pos, d1 = self._pick_insertion_point(msg, srefsyn)
            i = 0
            while i < len(msgpos_ins):
                omsg, opos = msgpos_ins[i]
                if pos < opos:
                    break
                elif cumulative:
                    pos += 1
            msgpos_ins.insert(i, (msg, pos))

        # Accumulate insertion positions if not cumulative.
        if not cumulative and len(msgpos_ins) > 1:
            off = 0
            msgpos_tmp = []
            for msg, pos in msgpos_ins:
                msgpos_tmp.append((msg, pos + off))
                off += 1
            msgpos_ins = msgpos_tmp

        # Update key-position links for the index to be added.
        off = 0
        for i in range(len(msgpos_ins)):
            pos1 = msgpos_ins[i][1] - off
            if i + 1 < len(msgpos_ins):
                pos2 = msgpos_ins[i + 1][1] - (off + 1)
            else:
                pos2 = len(self._messages)
            for j in range(pos1, pos2):
                ckey = self._messages[j].key
                self._msgpos[ckey] = j + (off + 1)
            off += 1

        # Insert messages at computed positions.
        for msg, pos in msgpos_ins:
            self._messages.insert(pos, msg)
            self._messages[pos]._remove_on_sync = False # no pending removal
            self._messages[pos]._committed = False # write it on sync
            self._msgpos[msg.key] = pos # store new key-position link
            self.__dict__["#"]["*"] += 1 # indicate sequence change

        # Replace existing messages.
        for msg in msgs_repl:
            pos = self._msgpos[msg.key]
            self._messages[pos] = msg

        # Recover insertion/replacement positions.
        pos_res = []
        msgpos_ins_d = dict(msgpos_ins)
        for msg, pos in msgpos:
            ipos = msgpos_ins_d.get(msg)
            if ipos is not None:
                pos_res.append(ipos)
            else:
                pos_res.append(None)

        return pos_res


    def obspos (self):
        """
        Get canonical position of the first obsolete message.

        I{Canonical} position of the first obsolete message is the position
        of first of the contiguous obsolete messages at the end of the catalog.
        Normally this should be the same as the position of the very first
        obsolete message, as all obsolete messages should be contiguously
        grouped at the end. But there is no enforcement of such grouping,
        therefore the more stricter definition.

        If there are no messages in the catalog, or the last message
        is not obsolete, the position is reported as number of messages
        (i.e. one position after the last message).

        Runtime complexity O(number of contiguous trailing obsolete messages).

        @return: canonical position of first obsolete message
        @rtype: int
        """

        op = len(self._messages)
        while op > 0 and self._messages[op - 1].obsolete:
            op -= 1

        return op


    def add_last (self, msg):
        """
        Add a message to the selected end of catalog, if not already in it.

        Synonym to C{cat.add(msg, cat.obspos())} if the message is
        not obsolete (i.e. tries to add the message after all non-obsolete),
        or to C{cat.add(msg, len(cat))} (tries to add at the very end).
        If the message already exits in the catalog (by key),
        same behavior as for L{add} applies.

        @see: L{add}
        """

        if not msg.obsolete:
            return self.add(msg, self.obspos())
        else:
            return self.add(msg, len(self._messages))


    def remove (self, ident):
        """
        Remove a message from the catalog, by position or another message.

        If the position is out of range, or the lookup message does not have
        a counterpart in this catalog with the same key, an error is signaled.

        Runtime complexity O(n), regardless of C{ident} type.
        Use L{remove_on_sync()<remove_on_sync>} method for O(1) complexity,
        when the logic allows the removal to be delayed to syncing time.

        @param ident: position index or another message
        @type ident: int or L{Message_base}

        @returns: C{None}
        """

        self._assert_headonly()

        # Determine position and key by given ident.
        if isinstance(ident, int):
            ip = ident
            key = self._messages[ip].key
        else:
            key = ident.key
            ip = self._msgpos[key]

        # Update key-position links for the removed index.
        for i in range(ip + 1, len(self._messages)):
            ckey = self._messages[i].key
            self._msgpos[ckey] = i - 1

        # Remove from messages and key-position links.
        self._messages.pop(ip)
        self._msgpos.pop(key)
        self.__dict__["#"]["*"] += 1 # indicate sequence change


    def remove_on_sync (self, ident):
        """
        Remove a message from the catalog, by position or another message,
        on the next sync.

        If the position is out of range, or the lookup message does not have
        a counterpart in this catalog with the same key, an error is signaled.

        Suited for for-in iterations over a catalog with a sync afterwards,
        so that the indices are not confused by removal, and good performance.

        Runtime complexity O(1).

        @param ident: position index or another message
        @type ident: int or L{Message_base}

        @returns: C{None}
        """

        self._assert_headonly()

        # Determine position and key by given ident.
        if isinstance(ident, int):
            ip = ident
        else:
            ip = self._msgpos[ident.key]

        # Indicate removal on sync for this message.
        self._messages[ip]._remove_on_sync = True
        self.__dict__["#"]["*"] += 1 # indicate sequence change (pending)


    def sync (self, force=False, noobsend=False, writefh=None, fitplural=False):
        """
        Write catalog file to disk if any message has been modified.

        All activities scheduled for sync-time are performed, such as
        delayed message removal.

        If catalog is monitored, unmodified messages (and message parts)
        are not reformatted unless forced.

        Instead of opening and writing into catalog's filename,
        catalog can be written to a file-like object provided by
        C{writefh} parameter.
        Same as when writing to file on disk, text will be encoded
        using catalog's encoding before writing it to C{writefh}.

        If in a plural message the number of C{msgstr} fields is not equal
        to the number specified in the plural header, the C{fitplural}
        parameter can be set to C{True} to correct this on syncing.
        However, this fitting will be performed only on clean plural messages,
        i.e. those in which all existing C{msgstr} fields are empty,
        as otherwise it is unclear how to adapt them to plural header.

        @param force: whether to reformat unmodified messages
        @type force: bool
        @param noobsend: do not reorder messages to group all obsolete at end
        @type noobsend: bool
        @param writefh: file to write the catalog to
        @type writefh: file-like object open in binary mode
        @param fitplural: whether to fit the number of msgstr fields in
            clean plural messages to plural header specification
        @type fitplural: bool

        @returns: C{True} if the file was modified, C{False} otherwise
        @rtype: bool
        """

        # Cannot sync catalogs which have been given no path
        # (usually temporary catalogs).
        if not self._filename.strip():
            raise PologyError(
                _("@info",
                  "Trying to sync unnamed catalog."))

        # Fit the number of msgstr entries in plural messages if requested.
        # Must be done before the modification test below.
        if fitplural:
            n = self.nplurals()
            for msg in self._messages:
                if (    msg.msgid_plural is not None
                    and len(msg.msgstr) != n
                    and all(len(s) == 0 for s in msg.msgstr)
                ):
                    msg.msgstr[:] = [""] * n

        # If catalog is not monitored, force syncing.
        if not self._monitored:
            force = True

        # If no modifications throughout and sync not forced, return.
        if not force and not self.modcount:
            return False

        # No need to indicate sequence changes here, as after sync the
        # catalog is set to unmodified throughout.

        # Temporarily insert header, for homogeneous iteration.
        self._messages.insert(0, self._header)
        self._messages[0]._remove_on_sync = False # never remove header
        nmsgs = len(self._messages)

        # Starting position for reinserting obsolete messages.
        obstop = len(self._messages)
        while obstop > 0 and self._messages[obstop - 1].obsolete:
            obstop -= 1
        obsins = obstop

        # NOTE: Key-position links may be invalidated from this point onwards,
        # by reorderings/removals. To make sure it is not used before the
        # rebuild at the end, delete now.
        del self._msgpos

        if not self._wrap_determined:
            self.wrapping()

        flines = []
        i = 0
        while i < nmsgs:
            msg = self._messages[i]
            if msg.get("_remove_on_sync", False):
                # Removal on sync requested, just skip.
                i += 1
            elif not noobsend and msg.obsolete and i < obstop:
                # Obsolete message out of order, reinsert and repeat the index.
                # Reinsertion is such that the relative ordering of obsolete
                # messages is preserved.
                msg = self._messages.pop(i)
                self._messages.insert(obsins - 1, msg) # -1 due to popping
                obstop -= 1
            else:
                # Normal message, append formatted lines to rest.
                committed = msg.get("_committed", False)
                flines.extend(msg.to_lines(self._wrapf,
                                           force or not committed))
                # Message should finish with one empty line.
                if flines[-1] != "\n":
                    flines.append("\n")
                i += 1
        if not self._tail:
            # Remove trailing empty lines.
            while flines and flines[-1] == "\n":
                flines.pop(-1)
        else:
            # Tail has to be converted to separate lines,
            # so that possibly new encoding is applied to it too
            # while being able to report line/column on error.
            flines.extend(x + "\n" for x in self._tail.split("\n"))
            if self._tail.endswith("\n"):
                flines.pop(-1)

        # Remove temporarily inserted header.
        self._messages.pop(0)

        # Update message map.
        self.sync_map()

        # Reset modification state throughout.
        self.modcount = 0

        # Encode lines and write file.
        enclines = []
        for i, line in enumerate(flines):
            try:
                encline = line.encode(self._encoding)
            except UnicodeEncodeError as e:
                raise CatalogSyntaxError(
                    _("@info",
                      "Text encoding failure at %(file)s:%(line)d:%(col)d "
                      "under assumed encoding '%(enc)s'.",
                      file=self._filename, line=(i + 1), col=e[2],
                      enc=self._encoding))
            enclines.append(encline)
        if not writefh:
            # Create the parent directory if it does not exist.
            pdirpath = os.path.dirname(self._filename)
            mkdirpath(pdirpath)
            # Write to file atomically: directly write to temporary file,
            # then rename it to destination file.
            #ofl = tempfile.NamedTemporaryFile(delete=False, dir=pdirpath)
            #tmpfname = ofl.name
            # ...needs Python 2.6
            tmpfname = os.path.join(pdirpath,
                                    os.path.basename(self._filename) + "~tmpw")
            ofl = open(tmpfname, "wb")
        else:
            ofl = writefh
        ofl.writelines(enclines)
        if not writefh:
            ofl.close()
            if os.name == "nt" and os.path.exists(self._filename):
                # NT does not allow to overwrite on rename.
                tmpfname2 = self._filename + "~tmpo"
                os.rename(self._filename, tmpfname2)
                os.rename(tmpfname, self._filename)
                os.remove(tmpfname2)
            else:
                os.rename(tmpfname, self._filename)

        # Indicate the catalog is no longer created from scratch, if it was.
        self._created_from_scratch = False

        # Indicate header has been committed.
        self._header._committed = True

        # Indicate for each message that it has been committed.
        for msg in self._messages:
            msg._committed = True

        return True


    def sync_map (self):
        """
        Update message map.

        In case there were any modifications to message keys,
        or any pending removals issued, this function will update
        the sequence of messages such that membership operations
        work properly again.
        Obsolete messages will be moved to end of catalog.
        Referent line and entry numbers will remain invalid,
        as catalog will not be written out.

        This is a less expensive alternative to syncing the catalog,
        when it is only necessary to continue using it in synced state,
        rather than actually writing it out.
        """

        # Execute pending removals.
        # Separate messages into current and obsolete.
        newlst = []
        newlst_obs = []
        for msg in self._messages:
            if not msg.get("_remove_on_sync", False):
                if not msg.obsolete:
                    newlst.append(msg)
                else:
                    newlst_obs.append(msg)
        newlst.extend(newlst_obs)
        self.__dict__["*"] = newlst
        self._messages = self.__dict__["*"]

        # Rebuild key-position links.
        self._msgpos = {}
        for i in range(len(self._messages)):
            self._msgpos[self._messages[i].key] = i

        # Set inverse map to non-computed.
        self._invmap = None


    def _make_invmap (self):

        # Map for inverse lookup (by translation) has as key the msgstr[0],
        # and the value the list of messages having the same msgstr[0].

        self._invmap = {}
        for msg in self._messages:
            ikey = msg.msgstr[0]
            msgs = self._invmap.get(ikey)
            if msgs is None:
                msgs = []
                self._invmap[ikey] = msgs
            msgs.append(msg)


    def insertion_inquiry (self, msg, srefsyn={}):
        """
        Compute the tentative insertion of the message into the catalog.

        The tentative insertion is a tuple of position of a message when it
        would be inserted into the catalog, and the I{weight} indicating
        the quality of positioning. The weight is computed by analyzing
        the source references.

        Runtime complexity O(n).

        @param msg: message to compute the tentative insertion for
        @type msg: L{Message_base}
        @param srefsyn: synonymous names to some of the source files
        @type srefsyn: {string: [string*]*}

        @returns: the insertion position and its weight
        @rtype: int, float
        """

        self._assert_headonly()
        return self._pick_insertion_point(msg, srefsyn)


    def created (self):
        """
        Whether the catalog has been newly created (no existing PO file).

        A catalog is no longer considered newly created after the first sync.

        @returns: C{True} if newly created, C{False} otherwise
        @rtype: bool
        """

        return self._created_from_scratch


    def _pick_insertion_point (self, msg, srefsyn={}):

        # Return the best insertion position with associated weight.
        # Assume the existing messages in the catalog are properly ordered.

        if not msg.obsolete:
            last = self.obspos()
        else:
            last = len(self._messages)

        # Insert at the last position if the candidate message has
        # no source references.
        if not msg.source:
            return last, 0.0

        ins_pos = -1
        # Try to find insertion position by comparing the source references
        # of the candidate the source references of the existing messages.
        # The order of matching must be very specific for logical insertion.
        # If the matching source files are found, insert according to
        # the line number.
        for src, lno in msg.source:
            src_pos = 0
            src_match = False
            curr_prim_esrc = ""
            for i in range(last):
                emsg = self._messages[i]
                if not emsg.source:
                    continue
                same_prim_esrc = False
                for esrc, elno in emsg.source:
                    if curr_prim_esrc in [esrc] + srefsyn.get(esrc, []):
                        same_prim_esrc = True
                        break
                if not same_prim_esrc:
                    curr_prim_esrc, elno = emsg.source[0]

                if src in [curr_prim_esrc] + srefsyn.get(curr_prim_esrc, []):
                    # The source file names match.
                    # Insert at this position if the candidate's line
                    # number preceeds that of the current message.
                    src_match = True
                    if lno < elno:
                        ins_pos = i
                        break
                elif src_match:
                    # The sources no longer match, but were matched
                    # before. This means the candidate line number is
                    # after all existing, so insert at this position.
                    ins_pos = i
                    break

                if ins_pos >= 0:
                    break

            if ins_pos >= 0:
                break

        if ins_pos >= 0:
            return ins_pos, 1.0
        else:
            return last, 0.0


    def nplurals (self):
        """
        Number of msgstr fields expected for plural messages.

        Determined by the Plural-Forms header field; if this field
        is absent from the header, defaults to 1.

        @returns: number of plurals
        @rtype: int
        """

        # Get nplurals string from the header.
        plforms = self._header.get_field_value("Plural-Forms")
        if not plforms: # no plural definition
            return 1
        nplustr = plforms.split(";")[0]

        # Get the number of forms from the string.
        m = re.search(r"\d+", nplustr)
        if not m: # malformed nplurals
            return 1

        return int(m.group(0))


    def plural_index (self, number):
        """
        Msgstr field index in plural messages for given number.

        Determined by the Plural-Forms header field; if this field
        is absent from the header, defaults to 0.

        @param number: the number to determine the plural form for
        @type number: int

        @returns: index of msgstr field
        @rtype: int
        """

        # Get plural definition from the header.
        plforms = self._header.get_field_value("Plural-Forms")
        if not plforms: # no plural definition, assume 0
            return 0
        plustr = plforms.split(";")[1]

        # Rebuild evaluation string only if changed to last invocation.
        if plustr != self._plustr:
            # Record raw plural definition for check on next call.
            self._plustr = plustr

            # Prepare Python-evaluable string out of the raw definition.
            plustr = plustr[plustr.find("=") + 1:] # remove plural= part
            p = -1
            evalstr = ""
            while 1:
                p = plustr.find("?")
                if p < 0:
                    evalstr += " " + plustr
                    break
                cond = plustr[:p]
                plustr = plustr[p + 1:]
                cond = cond.replace("&&", " and ")
                cond = cond.replace("||", " or ")
                evalstr += "(" + cond + ") and "
                p = plustr.find(":")
                body = plustr[:p]
                plustr = plustr[p + 1:]
                evalstr += "\"" + body + "\" or "
            if not evalstr.strip():
                evalstr = "0"

            # Record the current evaluable definition.
            self._plustr_eval = evalstr

        # Evaluate the definition.
        n = number # set eval context (plural definition uses n as variable)
        form = int(eval(self._plustr_eval))

        return form


    def plural_indices_single (self):
        """
        Indices of the msgstr fields which are used for single number only.

        @returns: msgstr indices used for single numbers
        @rtype: [int*]
        """

        # Get plural definition from the header.
        plforms = self._header.get_field_value("Plural-Forms")
        if not plforms: # no plural definition, assume 0
            return [0]
        plustr = plforms.split(";")[1]

        lst = re.findall(r"\bn\s*==\s*\d+\s*\)?\s*\?\s*(\d+)", plustr)
        if not lst and re.search(r"\bn\s*(!=|>|<)\s*\d+\s*([^?]|$)", plustr):
            lst = ["0"]

        return [int(x) for x in lst]


    def select_by_key (self, msgctxt, msgid, wobs=False):
        """
        Select message from the catalog by the fields that define its key.

        If matched, the message is returned as a single-element list, or
        an empty list when there is no match. This is so that the result
        of this method is in line with other C{select_*} methods.

        Runtime complexity as that of L{find}.

        @param msgctxt: the text of C{msgctxt} field
        @type msgctxt: string or C{None}
        @param msgid: the text of C{msgid} field
        @type msgid: string
        @param wobs: whether to include obsolete messages in selection
        @type wobs: bool

        @returns: selected messages
        @rtype: [L{Message_base}*]
        """

        m = MessageUnsafe({"msgctxt" : msgctxt, "msgid" : msgid})
        p = self.find(m, wobs)
        if p >= 0:
            return [self._messages[p]]
        else:
            return []


    def select_by_key_match (self, msgctxt, msgid, exctxt=False, exid=True,
                             case=True, wobs=False):
        """
        Select messages from the catalog by matching key-defining fields.

        Parameters C{msgctxt} and C{msgid} are either exact values,
        to be matched by equality against message fields,
        or regular expression strings. Parameters C{exctxt} and C{exid}
        control which kind of match it is, respectively.

        Runtime complexity O(n), unless all matches are exact,
        when as that of L{find}.

        @param msgctxt: the text or regex string of C{msgctxt} field
        @type msgctxt: string or C{None}
        @param msgid: the text or regex string of C{msgid} field
        @type msgid: string
        @param exctxt: C{msgctxt} is exact value if C{True}, regex if C{False}
        @type exctxt: bool
        @param exid: C{msgid} is exact value if C{True}, regex if C{False}
        @type exid: bool
        @param case: whether regex matching is case-sensitive
        @type case: bool
        @param wobs: whether to include obsolete messages in selection
        @type wobs: bool

        @returns: selected messages
        @rtype: [L{Message_base}*]
        """

        if exctxt and exid:
            return self.select_by_key(msgctxt, msgid, wobs=wobs)

        rxflags = re.U
        if not case:
            rxflags |= re.I
        if not exctxt:
            if msgctxt is not None:
                msgctxt_rx = re.compile(msgctxt, rxflags)
            else:
                # Force exact match if actually no context required.
                exctxt = True
        if not exid:
            msgid_rx = re.compile(msgid, rxflags)

        selected_msgs = []
        for msg in self._messages:
            if (    (wobs or not msg.obsolete)
                and (   (exid and msg.msgid == msgid)
                     or (not exid and msgid_rx.search(msg.msgid)))
                and (   (exctxt and msg.msgctxt == msgctxt)
                     or (not exctxt and msgctxt_rx.search(msg.msgctxt or "")))
            ):
                selected_msgs.append(msg)

        return selected_msgs


    def select_by_msgid (self, msgid, wobs=False):
        """
        Select messages from the catalog by matching C{msgid} field.

        Several messages may have the same C{msgid} field, due to different
        C{msgctxt} fields. Empty list is returned when there is no match.

        Runtime complexity O(n).

        @param msgid: the text of C{msgid} field
        @type msgid: string
        @param wobs: whether to include obsolete messages in selection
        @type wobs: bool

        @returns: selected messages
        @rtype: [L{Message_base}*]
        """

        selected_msgs = []
        for msg in self._messages:
            if (wobs or not msg.obsolete) and msg.msgid == msgid:
                selected_msgs.append(msg)

        return selected_msgs


    def select_by_msgid_fuzzy (self, msgid, cutoff=0.6, wobs=False):
        """
        Select messages from the catalog by near-matching C{msgid} field.

        The C{cutoff} parameter determines the minimal admissible similarity
        (1.0 fo exact match).

        The messages are returned ordered by decreasing similarity.

        Runtime complexity O(n) * O(length(msgid)*avg(length(msgids)))
        (probably).

        @param msgid: the text of C{msgid} field
        @type msgid: string
        @param cutoff: minimal similarity
        @type cutoff: float
        @param wobs: whether to include obsolete messages in selection
        @type wobs: bool

        @returns: selected messages
        @rtype: [L{Message_base}*]
        """

        # Build dictionary of message keys by msgid;
        # there can be several keys per msgid, pack in a list.
        msgkeys = {}
        for msg in self._messages:
            if msg.obsolete and not wobs:
                # Skip obsolete messages if not explicitly included.
                continue
            if msg.msgid not in msgkeys:
                msgkeys[msg.msgid] = []
            msgkeys[msg.msgid].append(msg.key)

        # Get near-match msgids.
        near_msgids = difflib.get_close_matches(msgid, msgkeys, cutoff=cutoff)

        # Collect messages per selected msgids.
        selected_msgs = []
        for near_msgid in near_msgids:
            for msgkey in msgkeys[near_msgid]:
                selected_msgs.append(self._messages[self._msgpos[msgkey]])

        return selected_msgs


    def select_by_msgstr (self, msgstr0, wobs=False, lazy=False):
        """
        Select messages from the catalog inversely, by their msgstr[0].

        Several messages may have the same C{msgstr[0]} field,
        so the return value is always a list of messages.
        Empty list is returned when there is no match.

        Runtime complexity is O(n) if C{lazy} is C{False}.
        If C{lazy} is C{True}, complexity is O(n) for the first search,
        and then O(1) until next syncing of the catalog;
        if msgstr fields of some messages change in between,
        or messages are added or removed from the catalog,
        this is not seen until next syncing.

        @param msgstr0: the text of C{msgstr[0]} field
        @type msgstr0: string
        @param wobs: whether to include obsolete messages in selection
        @type wobs: bool
        @param lazy: whether to assume msgstr are not modified between syncings
        @type lazy: bool

        @returns: selected messages
        @rtype: [L{Message_base}*]
        """

        if not lazy:
            selected_msgs = {}
            for msg in self._messages:
                if (wobs or not msg.obsolete) and msg.msgstr[0] == msgstr0:
                    selected_msgs.append(msg)
        else:
            if self._invmap is None:
                self._make_invmap()
            selected_msgs = self._invmap.get(msgstr0, [])
            if not wobs:
                selected_msgs = [x for x in selected_msgs if not x.obsolete]

        return selected_msgs


    def encoding (self):
        """
        Report encoding used when syncing the catalog.

        Encoding is determined from C{Content-Type} header field.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes.
        If you want to set encoding after the catalog has been
        opened, use L{set_encoding}.

        @returns: the encoding name
        @rtype: string
        """

        return self._encoding


    def set_encoding (self, encoding):
        """
        Set encoding used when syncing the catalog.

        Encoding set by this method will later be readable by
        the L{encoding} method.
        This will also modify the catalog header C{Content-Type} field.

        @param encoding: the encoding name
        @type encoding: string
        """

        self._encoding = encoding

        ctval = "text/plain; charset=%s" % encoding
        self.header.set_field("Content-Type", ctval)


    def accelerator (self):
        """
        Report characters used as accelerator markers in GUI messages.

        Accelerator characters are determined by looking for certain
        header fields, in this order: C{Accelerator-Marker},
        C{X-Accelerator-Marker}.
        In each field, several accelerator markers can be stated as
        comma-separated list, or there may be several fields;
        the union of all parsed markers is reported.

        If empty set is returned, it was determined that there are
        no accelerator markers in the catalog;
        if C{None}, that there is no determination about markers.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes.
        If you want to set accelerator markers after the catalog has been
        opened, use L{set_accelerator}.

        @returns: accelerator markers
        @rtype: set(string*) or C{None}
        """

        if self._accels_determined:
            return self._accels

        accels = None
        self._accels_determined = True

        for fname in (
            "Accelerator-Marker",
            "X-Accelerator-Marker",
        ):
            fields = self._header.select_fields(fname)
            for fname, fval in fields:
                if accels is None:
                    accels = set()
                accels.update([x.strip() for x in fval.split(",")])
        if accels:
            accels.discard("")

        self._accels = accels
        return accels


    def set_accelerator (self, accels):
        """
        Set accelerator markers that can be expected in messages.

        Accelerator markers set by this method will later be readable by
        the L{accelerator} method. This will not modify the catalog header
        in any way; if that is desired, it must be done manually by
        manipulating the header fields.

        If C{accels} is given as C{None}, it means the accelerator markers
        are undetermined; if empty, that there are no markers in messages.

        @param accels: accelerator markers
        @type accels: sequence of strings or C{None}
        """

        if accels is not None:
            self._accels = set(accels)
            self._accels.discard("")
        else:
            self._accels = None
        self._accels_determined = True


    def markup (self):
        """
        Report what types of markup can be expected in messages.

        Markup types are determined by looking for some header fields,
        which state markup types as short symbolic names,
        e.g. "html", "docbook", "mediawiki", etc.
        The header fields are tried in this order: C{Text-Markup},
        C{X-Text-Markup}.
        In each field, several markup types can be stated as
        comma-separated list.
        If there are several fields, it is undefined from which one
        markup names are collected.
        Markup names are always reported in lower-case, regardless
        of the original casing used in the header.
        See L{set_markup} for list of markup types currently observed
        by various Pology modules to influence processing behavior.

        If empty set is returned, it was determined that there is
        no markup in the catalog;
        if C{None}, that there is no determination about markup.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes.
        If you want to set markup types after the catalog has been
        opened, use L{set_markup} method.

        @returns: markup names
        @rtype: set(string*) or C{None}
        """

        if self._mtypes_determined:
            return self._mtypes

        mtypes = None
        self._mtypes_determined = True

        for fname in (
            "Text-Markup",
            "X-Text-Markup",
        ):
            fval = self._header.get_field_value(fname)
            if fval is not None:
                mtypes = set([x.strip().lower() for x in fval.split(",")])
                mtypes.discard("")

        self._mtypes = mtypes
        return mtypes


    def set_markup (self, mtypes):
        """
        Set markup types that can be expected in messages.

        Markup types set by this method will later be readable by
        the L{markup} method. This will not modify the catalog header
        in any way; if that is desired, it must be done manually by
        manipulating the header fields.

        If C{mtypes} is given as C{None}, it means the markup types
        are undetermined; if empty, that there is no markup in messages.

        The following markup types are currently used by various parts
        of Pology to influence behavior on processing:
          - C{html}: HTML 4.01
          - C{qtrich}: Qt rich-text, (almost) a subset of HTML
          - C{kuit}: UI semantic markup in KDE4
          - C{kde4}: markup in KDE4 UI POs, a mix of Qt rich-text and KUIT
          - C{docbook4}: Docbook 4.x markup, in documentation POs
          - C{xmlents}: only XML-like entities, no other formal markup

        @param mtypes: markup types
        @type mtypes: sequence of strings or C{None}
        """

        if mtypes is not None:
            self._mtypes = set([x.lower() for x in mtypes])
        else:
            self._mtypes = None
        self._mtypes_determined = True


    def language (self):
        """
        Report language of the translation.

        Language is determined by looking for the C{Language} header field.
        If this field is present, it should contain the language code
        in line with GNU C library locales, e.g. C{pt} for Portuguese,
        or C{pt_BR} for Brazilian Portuguese.
        If the field is not present, language is considered undetermined,
        and C{None} is returned.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes (most probably not).
        If you want to set language after the catalog has been
        opened, use L{set_language} method.

        @returns: language code
        @rtype: string or C{None}
        """

        if self._lang_determined:
            return self._lang

        lang = None
        self._lang_determined = True

        fval = self._header.get_field_value("Language")
        if fval:
            lang = fval.strip()

        self._lang = lang
        return lang


    def set_language (self, lang):
        """
        Set language of the translation.

        Language set by this method will later be readable by
        the L{language} method. This will not modify the catalog header
        in any way; if that is desired, it must be done manually by
        manipulating the header fields.

        If C{lang} is given as C{None}, it means the language is undetermined.
        If it is given as empty string, it means the language is deliberately
        considered unknown.

        @param lang: language code
        @type lang: string or C{None}
        """

        if lang is not None:
            self._lang = str(lang)
        else:
            self._lang = None
        self._lang_determined = True


    def environment (self):
        """
        Report environments which the catalog is part of.

        Sometimes the language alone is not enough to determine all
        the non-technical aspects of translation.
        For example, in a given language but different translation domains,
        one translator may decide to use one of the two synonyms naming a
        concept, and the other translator the other synonym.
        I{Environments} are a way to specify such sets of choices,
        so that automatic tools (e.g. terminology checker) can
        detect how to process a given catalog.

        An environment can represent anything.
        It may be a single translator, who applies own set of choices
        to all the catalogs under own maintenance;
        it may be a translation project, with many cooperating translators;
        and so on.
        Each environment is named by an alphanumeric keyword
        (such as normalized project name, translator's name, etc.),
        and should be unique within a given language.

        Environments are read from one of the following header fieldsE{:}
        C{Environment}, C{X-Environment}.
        The value the field should be comma-separated list of
        environment keywords.
        If there are several environment fields, it is undefined
        from which the environments are read.

        If more than one environment is stated, then wherever the conventions
        of two environments conflict, the environment mentioned later
        in the list should take precedence.
        For example, environment list such as C{"footp, jdoe"}
        would mean to apply conventions of FOO translation project,
        ammended by that of translator Johnas Doemann.

        It there is no environment header field, C{None} is reported.
        Empty list is reported if such field exists, but its value is empty.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes (most probably not).
        if you want to set environments after the catalog has been
        opened, use L{set_environment} method.

        @returns: environment keywords
        @rtype: [string*] or C{None}
        """

        if self._envs_determined:
            return self._envs

        envs = None
        self._envs_determined = True

        for fname in (
            "Environment",
            "X-Environment",
        ):
            fval = self._header.get_field_value(fname)
            if fval is not None:
                envs = [x.strip().lower() for x in fval.split(",")]
                while "" in envs:
                    envs.remove("")
                break

        self._envs = envs
        return envs


    def set_environment (self, envs):
        """
        Set environments which the catalog is part of.

        Environments set by this method will later be readable by
        the L{environment} method. This will not modify the catalog header
        in any way; if that is desired, it must be done manually by
        manipulating the header fields.

        If C{envs} is given as C{None}, it means that the environments
        are undetermined; if empty, the catalog belongs to no environment.

        @param envs: environment keywords
        @type envs: sequence of strings or C{None}
        """

        if envs is not None:
            self._envs = set([x.lower() for x in envs])
        else:
            self._envs = None
        self._envs_determined = True


    def wrapping (self):
        """
        Report wrapping policy for message fields.

        Long text fields in messages (C{msgid}, C{msgstr}, etc.) may
        be wrapped in different ways, as wrapping does not influence
        their semantics.
        (This is unlike translator and extracted comments, which are
        never wrapped, because division into lines may be significant.)
        PO processing tools will typically offer wrapping options,
        but it may be more convenient to have wrapping policy
        bound to the catalog, which tools respect unless overridden.

        The following header fields are checked for wrapping policy,
        in given order: C{Wrapping}, C{X-Wrapping}.
        Wrapping policy (i.e. value of these header fields) is
        an unordered comma-separated list of wrapping keywords.
        See L{select_field_wrapper<wrap.select_field_wrapper>}
        for possible keywords.
        If no wrapping policy field is found in the header,
        C{None} is returned.
        If several wrapping policy fields are present,
        it is undefined which one is taken into account.

        It is not defined when the header will be examined,
        or if it will be reexamined when it changes (most probably not).
        If you want to set wrapping after the catalog has been
        opened, use L{set_wrapping} method.

        @returns: wrapping keywords
        @rtype: (string...) or C{None}
        """

        if self._wrap_determined:
            return self._wrapkw

        wrapkw = None
        self._wrap_determined = True

        for fname in (
            "Wrapping",
            "X-Wrapping",
        ):
            fval = self._header.get_field_value(fname)
            if fval is not None:
                wrapkw = [x.strip().lower() for x in fval.split(",")]
                wrapkw = tuple(sorted(wrapkw))
                break

        self._wrapkw = wrapkw
        self._wrapf = select_field_wrapper(wrapkw)

        return self._wrapkw


    def set_wrapping (self, wrapkw):
        """
        Set wrapping policy for message fields.

        Wrapping policy set by this method will later be readable by
        the L{wrapping} method. This will not modify the catalog header
        in any way; if that is desired, it must be done manually by
        manipulating the header fields.

        Wrapping policy is a sequence of keywords.
        See L{select_field_wrapper<wrap.select_field_wrapper>}
        for possible keywords.
        If C{None} is given instead, it is passed directly to
        L{select_field_wrapper<wrap.select_field_wrapper>},
        which will construct default wrapper.

        @param wrapkw: wrapping policy
        @type wrapkw: [string...] or C{None}
        """

        self._wrapkw = tuple(sorted(wrapkw)) if wrapkw is not None else None
        self._wrapf = select_field_wrapper(wrapkw)
        self._wrap_determined = True


    def wrapf (self):
        """
        Get wrapping function used for message fields.

        Wrapping function is determined based on wrapping policy
        (see L{wrapping}, L{set_wrapping}).
        Wrapping function returned by this method is suitable as
        C{wrapf} parameter in methods of C{Message} objects.

        @returns: wrapping function
        @rtype: (string, string, string?)->[string]

        @see: L{wrap_field<wrap.wrap_field>}
        """

        self.wrapping()
        return self._wrapf


    def messages_by_source (self):
        """
        Get messages grouped as lists by source.

        All messages sharing the same primary source file
        (their first source reference) are grouped
        and filed under that source file path.
        Grouping is represented by list of tuples of
        (source, list of messages), with both sources and
        messages within partial lists ordered by appearance.

        @return: messages grouped by sources
        @rtype: [(string, [L{Message_base}])]
        """

        msgs_by_src = {}
        sources = []
        for msg in self._messages:
            src = msg.source and msg.source[0][0] or ""
            if src not in msgs_by_src:
                msgs_by_src[src] = []
                sources.append(src)
            msgs_by_src[src].append(msg)

        return [(x, msgs_by_src[x]) for x in sources]


    def sort_by_source (self):
        """
        Sort messages in catalog by source references.

        Source references within each message are sorted too,
        before messages are sorted by source references.

        If any message changed its position due to sorting,
        L{sync_map} is called at the end.
        """

        # Sort source references within messages.
        for msg in self._messages:
            sorted_source = sorted(msg.source,
                                   key=lambda s: (s[0].lower(), s[1]))
            if self._monitored:
                msg.source = Monlist(list(map(Monpair, sorted_source)))
            else:
                msg.source = sorted_source

        sorted_messages = sorted(self._messages,
                                 key=lambda m: [(s[0].lower(), s[1])
                                                for s in m.source[:1]])

        any_moved = False
        for i in range(len(self._messages)):
            if sorted_messages[i] is not self._messages[i]:
                any_moved = True
                break
        if any_moved:
            self._messages = sorted_messages
            self.sync_map()


    def update_header (self, project=None, title=None,
                       copyright=None, license=None,
                       name=None, email=None, teamemail=None,
                       langname=None, langcode=None,
                       encoding=None, ctenc=None,
                       plforms=None, poeditor=None):
        """
        Update catalog header.

        If a piece of information is not given (i.e. C{None}),
        the corresponding header field is left unmodified.
        If it is given as empty string, the corresponding header field
        is removed.
        PO revision date is updated always, to current date.

        Some fields (as noted in parameter descriptions) are expanded
        on variables by applying the
        L{expand_vars<pology.resolve.expand_vars>} function.
        For example::

            title="Translation of %project into %langname."

        The following variables are available:
          - C{%basename}: PO file base name
          - C{%poname}: PO file base name without .po extension
          - C{%project}: value of C{project} parameter (if not C{None}/empty)
          - C{%langname}: value of C{langname} parameter (if not C{None}/empty)
          - C{%langcode}: value of C{langcode} parameter (if not C{None}/empty)

        @param project: project name
        @type project: string
        @param title: translation title (expanded on variables)
        @type title: string
        @param copyright: copyright notice (expanded on variables)
        @type copyright: string
        @param license: license notice (expanded on variables)
        @type license: string
        @param name: translator's name
        @type name: string
        @param email: translator's email address
        @type email: string
        @param teamemail: language team's email address
        @type teamemail: string
        @param langname: full language name
        @type langname: string
        @param langcode: language code
        @type langcode: string
        @param encoding: text encoding
        @type encoding: string
        @param ctenc: content transfer encoding
        @type ctenc: string
        @param plforms: plural forms expression
        @type plforms: string
        @param poeditor: translator's PO editor
        @type poeditor: string

        @returns: reference to header
        """

        varmap = {}
        varmap["basename"] = os.path.basename(self.filename)
        varmap["poname"] = self.name
        if project:
            varmap["project"] = project
        if langname:
            varmap["langname"] = langname
        if langcode:
            varmap["langcode"] = langcode
        varhead="%"

        hdr = self.header

        if title:
            title = expand_vars(title, varmap, varhead)
            hdr.title[:] = [str(title)]
        elif title == "":
            hdr.title[:] = []

        if copyright:
            copyright = expand_vars(copyright, varmap, varhead)
            hdr.copyright = str(copyright)
        elif copyright == "":
            hdr.copyright = None

        if license:
            license = expand_vars(license, varmap, varhead)
            hdr.license = str(license)
        elif license == "":
            hdr.license = None

        if project:
            hdr.set_field("Project-Id-Version", str(project))
        elif project == "":
            hdr.remove_field("Project-Id-Version")

        hdr.set_field("PO-Revision-Date", format_datetime())

        if name or email:
            if name and email:
                tr_ident = "%s <%s>" % (name, email)
            elif name:
                tr_ident = "%s" % name
            else:
                tr_ident = "<%s>" % email

            # Remove author placeholder.
            for i in range(len(hdr.author)):
                if "FIRST AUTHOR" in hdr.author[i]:
                    hdr.author.pop(i)
                    break

            # Look for current author in the comments,
            # to update only years if present.
            cyear = time.strftime("%Y")
            acfmt = "%s, %s."
            new_author = True
            for i in range(len(hdr.author)):
                if tr_ident in hdr.author[i]:
                    # Parse the current list of years.
                    years = re.findall(r"\b(\d{2,4})\s*[,.]", hdr.author[i])
                    if cyear not in years:
                        years.append(cyear)
                    years.sort()
                    hdr.author[i] = acfmt % (tr_ident, ", ".join(years))
                    new_author = False
                    break
            if new_author:
                hdr.author.append(acfmt % (tr_ident, cyear))

            hdr.set_field("Last-Translator", str(tr_ident))

        elif name == "" or email == "":
            hdr.remove_field("Last-Translator")

        if langname:
            tm_ident = None
            if langname and teamemail:
                tm_ident = "%s <%s>" % (langname, teamemail)
            elif langname:
                tm_ident = langname
            hdr.set_field("Language-Team", str(tm_ident))
        elif langname == "":
            hdr.remove_field("Language-Team")

        if langcode:
            hdr.set_field("Language", str(langcode), after="Language-Team")
        elif langcode == "":
            hdr.remove_field("Language")

        if encoding:
            ctval = "text/plain; charset=%s" % encoding
            hdr.set_field("Content-Type", ctval)
        elif encoding == "":
            hdr.remove_field("Content-Type")

        if ctenc:
            hdr.set_field("Content-Transfer-Encoding", str(ctenc))
        elif ctenc == "":
            hdr.remove_field("Content-Transfer-Encoding")

        if plforms:
            hdr.set_field("Plural-Forms", str(plforms))
        elif plforms == "":
            hdr.remove_field("Plural-Forms")

        if poeditor:
            hdr.set_field("X-Generator", str(poeditor))
        elif poeditor == "":
            hdr.remove_field("X-Generator")

        return hdr


    def detect_renamed_sources (self, cat, minshare=0.7):
        """
        Heuristically determine possible renamings of source files
        from this catalog based on source files in the other catalog.

        To determine the possibility that the source file A from this catalog
        has been renamed into source file B in the other catalog C{cat},
        primarily the share of common messages to A and B is considered.
        The minimum needed commonality can be given by C{minshare} parameter.

        When a source file from this catalog is directly mentioned in
        the other catalog, it is immediatelly considered to have
        no possible renamings.

        The return value is a dictionary in which the key is
        the source file and the value is the list of its possible
        renamed counterparts.
        The renaming list is never empty, i.e. if no renamings
        were detected for a given source file, that source file
        will not be present in the dictionary.
        The dictionary is fully symmetric: if source file B is in
        the renaming list of file A, then there will be
        an entry for file B with A in its renaming list
        (even when B is comming from the other catalog).

        Instead of a single other catalog to test against,
        a sequence of several other catalogs can be given.

        @param cat: catalog against which to test for renamings
        @type cat: Catalog or [Catalog*]
        @param minshare: the minimum commonality between two source files
            to consider them as possible renaming pair (0.0-1.0)
        @type minshare: float

        @returns: the renaming dictionary
        @rtype: {string: [string*]*}
        """

        renamings = {}

        # Collect all own sources, to avoid matching for them.
        ownfs = set()
        for msg in self._messages:
            for src, lno in msg.source:
                ownfs.add(src)

        if isinstance(cat, Catalog):
            cats = [cat]
        else:
            cats = cat

        for ocat in cats:
            if self is ocat:
                continue

            fcnts = {}
            ccnts = {}
            for msg in self._messages:
                omsg = ocat.get(msg)
                if omsg is None:
                    continue
                for src, lno in msg.source:
                    if src not in fcnts:
                        fcnts[src] = 0.0
                        ccnts[src] = {}
                    # Weigh each message disproportionally to the number of
                    # files it appears in (i.e. the sum of counts == 1).
                    fcnts[src] += 1.0 / len(msg.source)
                    counted = {}
                    for osrc, olno in omsg.source:
                        if osrc not in ownfs and osrc not in counted:
                            if osrc not in ccnts[src]:
                                ccnts[src][osrc] = 0.0
                            ccnts[src][osrc] += 1.0 / len(omsg.source)
                            counted[osrc] = True

            # Select match groups.
            fuzzies = {}
            for src, fcnt in sorted(fcnts.items()):
                shares = []
                for osrc, ccnt in sorted(ccnts[src].items()):
                    share = ccnt / (fcnt + 1.0) # tip a bit to avoid fcnt of 0.x
                    if share >= minshare:
                        shares.append((osrc, share))
                if shares:
                    shares.sort(key=lambda x: x[1]) # not necessary atm
                    fuzzies[src] = [f for f, s in shares]

            # Update the dictionary of renamings.
            for src, fuzzsrcs in sorted(fuzzies.items()):
                group = [src] + fuzzsrcs
                for src in group:
                    if src not in renamings:
                        renamings[src] = []
                    for osrc in group:
                        if src != osrc and osrc not in renamings[src]:
                            renamings[src].append(osrc)
                    if not renamings[src]:
                        renamings.pop(src)

        return renamings

