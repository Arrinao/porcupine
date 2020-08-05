import dataclasses
import functools
import tkinter
import tkinter.font as tkfont
import typing

import pygments.styles          # type: ignore

from porcupine import filetypes, settings, utils


@dataclasses.dataclass
class Change:
    # start and end are 'line.column' text index strings
    start: str
    end: str
    old_text_len: int
    new_text: str


# unfortunately a stupid class like this is necessary for passing a list of
# data classes into an event
@dataclasses.dataclass
class Changes(utils.EventDataclass):
    change_list: typing.List[Change]


class HandyText(tkinter.Text):
    """
    This class inherits from ``tkinter.Text`` and adds handy features.

    All ``kwargs`` are passed to ``tkinter.Text``.
    If you want to understand ``create_peer_from``, start by reading the
    ``PEER WIDGETS`` section in
    `text(3tk) <https://www.tcl.tk/man/tcl8.7/TkCmd/text.htm>`_.
    Passing ``create_peer_from=foo`` creates a text widget that is a peer of
    another text widget named ``foo``,
    which is useful for e.g. :source:`porcupine/plugins/overview.py`.

    .. virtualevent:: ContentChanged

        This event is generated when the text in the widget is modified
        in any way. Unlike ``<<Modified>>``, this event is simply generated
        every time the content changes, and there's no need to unset a flag
        like ``textwidget.edit_modified(False)`` or anything like that.

        If you want to know what changed and how, use
        :func:`porcupine.utils.bind_with_data` and
        ``event.data_class(Changes)``. For example, this...
        ::

            # let's say that text widget contains 'hello world'
            textwidget.replace('1.0', '1.5', 'toot')

        ...changes the ``'hello'`` to ``'toot'``, generating a
        ``<<ContentChanged>>`` event whose ``.data_class(Changes)`` returns
        a :class:`Changes` object like this::

            Changes(change_list=[
                Change(start='1.0', end='1.5', old_text_len=5, new_text='toot'),
            ])

        The ``<<ContentChanged>>`` event occurs after the text in the text
        widget has already changed. Also, sometimes many changes are applied
        at once and ``change_list`` contains more than one item.

    .. virtualevent:: CursorMoved

        This event is generated every time the user moves the cursor or
        it's moved with a method of the text widget. Use
        ``textwidget.index('insert')`` to find the current cursor
        position.
    """

    def __init__(self, master: tkinter.Widget, *,
                 create_peer_from: typing.Optional[tkinter.Text] = None,
                 **kwargs: typing.Any) -> None:
        super().__init__(master, **kwargs)

        if create_peer_from is not None:
            # Peer widgets are weird in tkinter. Text.peer_create takes in a
            # widget Tcl name, and then creates a peer widget with that name.
            # But if you want to create a tkinter widget, then you need to let
            # the tkinter widget to create a Tcl widget with a name chosen by
            # tkinter. That has happened above with the super() call. However,
            # rest of what happens in this __init__ method must do stuff to the
            # peer widget, rather than the widget that tkinter created.
            #
            # peer_create type is ignored because there's no good way to deal
            # with kwargs in mypy yet:
            #
            #    https://github.com/python/mypy/issues/6552
            #
            # Can't do self.destroy() because that screws up winfo_children().
            # Each tkinter widget has keeps a list of child widgets, and
            # self.destroy() would delete the widget from there. Tkinter's
            # winfo_children() also ignores anything not found in those lists.
            self.tk.call('destroy', str(self))  # destroy tkinter-created widget
            create_peer_from.peer_create(str(self), **kwargs)   # type: ignore
            utils.forward_event('<<ContentChanged>>', self, create_peer_from)

        #       /\
        #      /  \  WARNING: serious tkinter magic coming up
        #     / !! \          proceed at your own risk
        #    /______\
        #
        # this irc conversation might give you an idea of how this works:
        #
        #    <Akuli> __Myst__, why do you want to know how it works?
        #    <__Myst__> Akuli: cause it seems cool
        #    <Akuli> there's 0 reason to docment it in the langserver
        #    <Akuli> ok i can explain :)
        #    <Akuli> in tcl, all statements are command calls
        #    <Akuli> set x lol    ;# set variable x to string lol
        #    <Akuli> set is a command, x and lol are strings
        #    <Akuli> adding stuff to widgets is also command calls
        #    <Akuli> .textwidget insert end hello   ;# add hello to the text
        #            widget
        #    <Akuli> my magic renames the textwidget command to
        #            actual_widget_command, and creates a fake text widget
        #            command that tkinter calls instead
        #    <Akuli> then this fake command checks for all possible widget
        #            commands that can move the cursor or change the content
        #    <Akuli> making sense?
        #    <__Myst__> ooh
        #    <__Myst__> so it's like you're proxying actual calls to the text
        #               widget and calculating change events based on that?
        #    <Akuli> yes
        #    <__Myst__> very cool

        # cursor_cb is called whenever the cursor position may have changed,
        # and change_cb is called whenever the content of the text widget may
        # have changed
        change_cb_command = self.register(self._change_cb)
        cursor_cb_command = self.register(self._cursor_cb)

        # all widget stuff is implemented in python and in tcl as calls to a
        # tcl command named str(self), and replacing that with a custom command
        # is a very powerful way to do magic; for example, moving the cursor
        # with arrow keys calls the 'mark set' widget command :D
        actual_widget_command = str(self) + '_actual_widget'
        self.tk.call('rename', str(self), actual_widget_command)

        # this part is tcl because i couldn't get a python callback to work
        self.tk.eval('''
        proc %(fake_widget)s {args} {
            #puts $args

            # subcommand is e.g. insert, delete, replace, index, search, ...
            # see text(3tk) for all possible subcommands
            set subcommand [lindex $args 0]

            # issue #5: don't let the cursor to go to the very top or bottom of
            # the view
            if {$subcommand == "see"} {
                # cleaned_index is always a "LINE.COLUMN" string
                set cleaned_index [%(actual_widget)s index [lindex $args 1]]

                # from text(3tk): "If index is far out of view, then the
                # command centers index in the window." and we want to center
                # it correctly, so first go to the center, then a few
                # characters around it, and finally back to center because it
                # feels less error-prone that way
                %(actual_widget)s see $cleaned_index
                %(actual_widget)s see "$cleaned_index - 4 lines"
                %(actual_widget)s see "$cleaned_index + 4 lines"
                %(actual_widget)s see $cleaned_index
                return
            }

            set cursor_may_have_moved 0

            # only these subcommands can change the text, but they can also
            # move the cursor by changing the text before the cursor
            if {$subcommand == "delete" ||
                    $subcommand == "insert" ||
                    $subcommand == "replace"} {
                set cursor_may_have_moved 1

                # this is like self._change_cb(*args) in python
                %(change_cb)s {*}$args
            }

            # it's important that this comes after the change cb stuff because
            # this way it's possible to get old_length in self._change_cb()...
            # however, it's also important that this is before the mark set
            # stuff because the documented way to access the new index in a
            # <<CursorMoved>> binding is getting it directly from the widget
            set result [%(actual_widget)s {*}$args]

            # only[*] 'textwidget mark set insert new_location' can change the
            # cursor position, because the cursor position is implemented as a
            # mark named "insert" and there are no other commands that move
            # marks
            #
            # [*] i lied, hehe >:D MUHAHAHA ... inserting text before the
            # cursor also changes it
            if {$subcommand == "mark" &&
                    [lindex $args 1] == "set" &&
                    [lindex $args 2] == "insert"} {
                set cursor_may_have_moved 1
            }

            if {$cursor_may_have_moved} {
                %(cursor_cb)s
            }

            return $result
        }
        ''' % {
            'fake_widget': str(self),
            'actual_widget': actual_widget_command,
            'change_cb': change_cb_command,
            'cursor_cb': cursor_cb_command,
        })

        # see _cursor_cb
        self._old_cursor_pos = self.index('insert')

    def _create_change(
            self, start: str, end: str, new_text: str) -> Change:
        return Change(
            start=start,
            end=end,
            old_text_len=len(self.get(start, end)),
            new_text=new_text,
        )

    def _change_cb(self, subcommand: str, *args_tuple: str) -> None:
        changes: typing.List[Change] = []

        # search for 'pathName delete' in text(3tk)... it's a wall of text,
        # and this thing has to implement every detail of that wall
        if subcommand == 'delete':
            # "All indices are first checked for validity before any deletions
            # are made." they are already validated, but this doesn't hurt
            # imo... but note that rest of this code assumes that this is done!
            # not everything works in corner cases without this
            args = [self.index(arg) for arg in args_tuple]

            # tk has a funny abstraction of an invisible newline character at
            # the end of file, it's always there but nothing else uses it, so
            # let's ignore it
            for index, old_arg in enumerate(args):
                if old_arg == self.index('end'):
                    args[index] = self.index('end - 1 char')

            # "If index2 is not specified then the single character at index1
            # is deleted." and later: "If more indices are given, multiple
            # ranges of text will be deleted." but no mention about combining
            # these features, this works like the text widget actually behaves
            if len(args) % 2 == 1:
                args.append(self.index('%s + 1 char' % args[-1]))
            assert len(args) % 2 == 0
            pairs = list(zip(args[0::2], args[1::2]))

            # "If index2 does not specify a position later in the text than
            # index1 then no characters are deleted."
            pairs = [(start, end) for (start, end) in pairs
                     if self.compare(start, '<', end)]

            # "They [index pairs, aka ranges] are sorted [...]."
            # TODO: use the fact that (line, column) tuples sort nicely?
            def sort_by_range_beginnings(
                    range1: typing.Tuple[str, str],
                    range2: typing.Tuple[str, str]) -> int:
                start1, junk = range1
                start2, junk = range2
                if self.compare(start1, '>', start2):
                    return 1
                if self.compare(start1, '<', start2):
                    return -1
                return 0

            pairs.sort(key=functools.cmp_to_key(sort_by_range_beginnings))

            # "If multiple ranges with the same start index are given, then the
            # longest range is used. If overlapping ranges are given, then they
            # will be merged into spans that do not cause deletion of text
            # outside the given ranges due to text shifted during deletion."
            def merge_index_ranges(
                    start1: str, end1: str,
                    start2: str, end2: str) -> typing.Tuple[str, str]:
                start = start1 if self.compare(start1, '<', start2) else start2
                end = end1 if self.compare(end1, '>', end2) else end2
                return (start, end)

            # loop through pairs of pairs
            for i in range(len(pairs)-2, -1, -1):
                (start1, end1), (start2, end2) = pairs[i:i+2]
                if self.compare(end1, '>=', start2):
                    # they overlap
                    new_pair = merge_index_ranges(start1, end1, start2, end2)
                    pairs[i:i+2] = [new_pair]

            # "[...] and the text is removed from the last range to the first
            # range so deleted text does not cause an undesired index shifting
            # side-effects."
            for start, end in reversed(pairs):
                changes.append(self._create_change(start, end, ''))

        # the man page's inserting section is also kind of a wall of
        # text, but not as bad as the delete
        elif subcommand == 'insert':
            text_index, *other_args = args_tuple
            text_index = self.index(text_index)

            # "If index refers to the end of the text (the character after the
            # last newline) then the new text is inserted just before the last
            # newline instead."
            if text_index == self.index('end'):
                text_index = self.index('end - 1 char')

            # we don't care about the tagList arguments to insert, but we need
            # to handle the other arguments nicely anyway: "If multiple
            # chars-tagList argument pairs are present, they produce the same
            # effect as if a separate pathName insert widget command had been
            # issued for each pair, in order. The last tagList argument may be
            # omitted." i'm not sure what "in order" means here, but i tried
            # it, and 'textwidget.insert('1.0', 'asd', [], 'toot', [])' inserts
            # 'asdtoot', not 'tootasd'
            new_text = ''.join(other_args[::2])

            changes.append(self._create_change(
                text_index, text_index, new_text))

        # an even smaller wall of text that mostly refers to insert and replace
        elif subcommand == 'replace':
            start, end, *other_args = args_tuple
            start = self.index(start)
            end = self.index(end)
            new_text = ''.join(other_args[::2])

            # more invisible newline garbage
            if start == self.index('end'):
                start = self.index('end - 1 char')
            if end == self.index('end'):
                end = self.index('end - 1 char')

            # didn't find in docs, but tcl throws an error for this
            assert self.compare(start, '<=', end)

            changes.append(self._create_change(start, end, new_text))

        else:   # pragma: no cover
            raise ValueError(
                "the tcl code called _change_cb with unexpected subcommand: "
                + subcommand)       # noqa

        # remove changes that don't actually do anything
        changes = [
            change for change in changes
            if (change.start != change.end
                or change.old_text_len != 0
                or change.new_text)
        ]

        # some plugins expect <<ContentChanged>> events to occur after changing
        # the content in the editor, but the tcl code in __init__ needs them to
        # run before, so here is the solution
        if changes:
            self.after_idle(lambda: self.event_generate(
                '<<ContentChanged>>', data=Changes(changes)))

    def _cursor_cb(self) -> None:
        # more implicit newline stuff
        new_pos = self.index('insert')
        if new_pos == self.index('end'):
            new_pos = self.index('end - 1 char')

        if new_pos != self._old_cursor_pos:
            self._old_cursor_pos = new_pos
            self.event_generate('<<CursorMoved>>')

    def iter_chunks(self, n: int = 100) -> typing.Iterable[str]:
        r"""Iterate over the content as chunks of *n* lines.

        Each yielded line ends with a ``\n`` character. Lines are not
        broken down the middle, and ``''`` is never yielded.

        Note that the last chunk is less than *n* lines long unless the
        total number of lines is divisible by *n*.
        """
        start = 1     # this is not a mistake, line numbers start at 1
        while True:
            end = start + n
            if self.index('%d.0' % end) == self.index('end'):
                # '%d.0' % start can be 'end - 1 char' in a corner
                # case, let's not yield an empty string
                last_chunk = self.get('%d.0' % start, 'end - 1 char')
                if last_chunk:
                    yield last_chunk
                break

            yield self.get('%d.0' % start, '%d.0' % end)
            start = end

    def iter_lines(self) -> typing.Iterable[str]:
        r"""Iterate over the content as lines.

        The trailing ``\n`` characters of each line are included.
        """
        for chunk in self.iter_chunks():
            yield from chunk.splitlines(keepends=True)


# this can be used for implementing other themed things too, e.g. the
# line number plugin
class ThemedText(HandyText):
    """A :class:`.HandyText` subclass that uses the Pygments style's colors.

    You can use this class just like :class:`.HandyText`, it takes care
    of switching the colors by itself. This is useful for things like
    :source:`porcupine/plugins/linenumbers.py`.

    .. seealso::
        Syntax highlighting is implemented with Pygments in
        :source:`porcupine/plugins/highlight.py`.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self.bind('<<SettingsChanged:pygments_style>>', self._on_style_changed, add=True)
        self._on_style_changed()

    def _on_style_changed(self, junk: object = None) -> None:
        style = pygments.styles.get_style_by_name(settings.get('pygments_style', str))
        bg = style.background_color

        # yes, style.default_style can be '#rrggbb', '' or nonexistent
        # this is undocumented
        #
        #   >>> from pygments.styles import *
        #   >>> [getattr(get_style_by_name(name), 'default_style', '???')
        #   ...  for name in get_all_styles()]
        #   ['', '', '', '', '', '', '???', '???', '', '', '', '',
        #    '???', '???', '', '#cccccc', '', '', '???', '', '', '', '',
        #    '#222222', '', '', '', '???', '']
        fg = getattr(style, 'default_style', '') or utils.invert_color(bg)
        self.set_colors(fg, bg)

    # TODO: document this
    def set_colors(self, foreground: str, background: str) -> None:
        """
        This method runs automatically when the Pygments color style is
        changed. By default, it configures some text widget options.

        See :source:`porcupine/plugins/overview.py` for an example of
        overriding ``set_colors()``.
        """
        self['fg'] = foreground
        self['bg'] = background
        self['insertbackground'] = foreground  # cursor color

        self['selectforeground'] = background
        self['selectbackground'] = foreground


class MainText(ThemedText):
    """Don't use this. It may be changed later."""

    # the filetype is needed for setting the tab width and indenting
    def __init__(
            self,
            parent: tkinter.Widget,
            filetype: filetypes.FileType,
            **kwargs: typing.Any) -> None:
        super().__init__(parent, **kwargs)
        self.set_filetype(filetype)

        # FIXME: lots of things have been turned into plugins, but
        # there's still wayyyy too much stuff in here...
        partial = functools.partial     # pep8 line length
        self.bind('<BackSpace>', partial(self._on_delete, False))
        self.bind('<Control-BackSpace>', partial(self._on_delete, True))
        self.bind('<Control-Delete>', partial(self._on_delete, True))
        self.bind('<Shift-Control-Delete>',
                  partial(self._on_delete, True, shifted=True))
        self.bind('<Shift-Control-BackSpace>',
                  partial(self._on_delete, True, shifted=True))
        self.bind('<parenright>', self._on_closing_brace, add=True)
        self.bind('<bracketright>', self._on_closing_brace, add=True)
        self.bind('<braceright>', self._on_closing_brace, add=True)

        # most other things work by default, but these don't
        self.bind('<Control-v>', self._paste)
        self.bind('<Control-y>', self._redo)
        self.bind('<Control-a>', self._select_all)

    def set_filetype(self, filetype: filetypes.FileType) -> None:
        self._filetype = filetype

        # from the text(3tk) man page: "To achieve a different standard
        # spacing, for example every 4 characters, simply configure the
        # widget with “-tabs "[expr {4 * [font measure $font 0]}] left"
        # -tabstyle wordprocessor”."
        #
        # my version is kind of minimal compared to that example, but it
        # seems to work :)
        font = tkfont.Font(name='TkFixedFont', exists=True)
        self['tabs'] = [str(font.measure(' ' * filetype.indent_size))]

    def _on_delete(self, control_down: bool, event: tkinter.Event,
                   shifted: bool = False) -> utils.BreakOrNone:
        """This runs when the user presses backspace or delete."""
        if not self.tag_ranges('sel'):
            # nothing is selected, we can do non-default stuff
            if control_down and shifted:
                # plan A: delete until end or beginning of line
                # plan B: delete a newline character if there's nothing
                #         to delete with plan A
                if event.keysym == 'Delete':
                    plan_a = ('insert', 'insert lineend')
                    plan_b = ('insert', 'insert + 1 char')
                else:
                    plan_a = ('insert linestart', 'insert')
                    plan_b = ('insert - 1 char', 'insert')

                if self.index(plan_a[0]) == self.index(plan_a[1]):
                    # nothing can be deleted with plan a
                    self.delete(*plan_b)
                else:
                    self.delete(*plan_a)
                return 'break'

            if event.keysym == 'BackSpace':
                lineno = int(self.index('insert').split('.')[0])
                before_cursor = self.get('%d.0' % lineno, 'insert')
                if before_cursor and before_cursor.isspace():
                    self.dedent('insert')
                    return 'break'

                if control_down:
                    # delete previous word
                    end = self.index('insert')
                    self.event_generate('<<PrevWord>>')
                    self.delete('insert', end)
                    return 'break'

            if event.keysym == 'Delete' and control_down:
                # delete next word
                old_cursor_pos = self.index('insert')
                self.event_generate('<<NextWord>>')
                self.delete(old_cursor_pos, 'insert')
                return 'break'

        return None

    def _on_closing_brace(self, event: tkinter.Event) -> None:
        """Dedent automatically."""
        self.dedent('insert')

    def indent(self, location: str) -> None:
        """Insert indentation character(s) at the given location."""
        if not self._filetype.tabs2spaces:
            self.insert(location, '\t')
            return

        # we can't just add ' '*self._filetype.indent_size, for example,
        # if indent_size is 4 and there are 7 charaters we add 1 space
        spaces = self._filetype.indent_size    # pep-8 line length
        how_many_chars = int(self.index(location).split('.')[1])
        spaces2add = spaces - (how_many_chars % spaces)
        self.insert(location, ' ' * spaces2add)

    def dedent(self, location: str) -> bool:
        """Remove indentation character(s) if possible.

        This method tries to remove spaces intelligently so that
        everything's lined up evenly based on the indentation settings.
        This method is useful for dedenting whole lines (with location
        set to beginning of the line) or deleting whitespace in the
        middle of a line.

        This returns True if something was done, and False otherwise.
        """
        if not self._filetype.tabs2spaces:
            one_back = '%s - 1 char' % location
            if self.get(one_back, location) == '\t':
                self.delete(one_back, location)
                return True
            return False

        lineno, column = map(int, self.index(location).split('.'))
        line = self.get('%s linestart' % location, '%s lineend' % location)

        if column == 0:
            start = 0
            end = self._filetype.indent_size
        else:
            start = column - (column % self._filetype.indent_size)
            if start == column:    # prefer deleting from left side
                start -= self._filetype.indent_size
            end = start + self._filetype.indent_size

        end = min(end, len(line))    # don't go past end of line
        if start == 0:
            # delete undersized indents
            whitespaces = len(line) - len(line.lstrip())
            end = min(whitespaces, end)

        if not line[start:end].isspace():   # ''.isspace() is False
            return False
        self.delete('%d.%d' % (lineno, start), '%d.%d' % (lineno, end))
        return True

    def _redo(self, event: tkinter.Event) -> utils.BreakOrNone:
        self.event_generate('<<Redo>>')
        return 'break'

    def _paste(self, event: tkinter.Event) -> utils.BreakOrNone:
        self.event_generate('<<Paste>>')

        # by default, selected text doesn't go away when pasting
        try:
            sel_start, sel_end = self.tag_ranges('sel')
        except ValueError:
            # nothing selected
            pass
        else:
            self.delete(sel_start, sel_end)

        return 'break'

    def _select_all(self, event: tkinter.Event) -> utils.BreakOrNone:
        self.tag_add('sel', '1.0', 'end - 1 char')
        return 'break'
