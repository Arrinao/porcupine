import sys
import functools
import queue

import kieli

import porcupine
from .utils import (
    tab_uri,
    tab_text,
    tab_position,
    lsp_pos_to_tk_pos,
    tk_pos_to_lsp_pos,
    find_overlap_start,
)


class Client:
    SERVER_COMMANDS = {"Python": [sys.executable, "-m", "pyls"]}

    def __init__(self, tab):
        self.tab = tab

        self._version = 0

        self._client = kieli.LSPClient()

        self._client.notification_handler(
            "textDocument/publishDiagnostics", print
        )

        command = self.SERVER_COMMANDS[self.tab.filetype.name]
        if command is None:
            print("No command is known for", self.tab.filetype.name)
            return
        self._client.connect_to_process(*command)

        self._coroutines = queue.Queue()
        self._start_kernel()
        self._start_coroutine(self._initialize_lsp)

    def _start_kernel(self):
        root = porcupine.utils.get_main_window()
        interval = 100

        def worker():
            while True:
                try:
                    coro, value = self._coroutines.get_nowait()
                except queue.Empty:
                    break

                try:
                    client_call = coro.send(value)
                except StopIteration:
                    # XXX: Should we give the return value to someone?
                    continue

                if client_call["action"] == "request":
                    self._client.request(
                        client_call["method"], client_call["params"]
                    )

                    # XXX: Should we define this somewhere else?
                    def callback(coro, request, response):
                        self._coroutines.put((coro, (request, response)))

                    self._client.response_handler(
                        client_call["method"],
                        functools.partial(callback, coro),
                    )
                elif client_call["action"] == "notify":
                    self._client.notify(
                        client_call["method"], client_call["params"]
                    )
                    self._coroutines.put((coro, None))
                else:
                    raise RuntimeError(
                        "Unsupported action %r." % client_call["action"]
                    )

            root.after(interval, worker)

        root.after(interval, worker)

    def _start_coroutine(self, func, *args, **kwargs):
        coro = func(*args, **kwargs)
        self._coroutines.put((coro, None))

    # coroutine
    def _initialize_lsp(self):
        yield {
            "action": "request",
            "method": "initialize",
            "params": {"rootUri": None, "processId": None, "capabilities": {}},
        }

        yield {
            "action": "notify",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": tab_uri(self.tab),
                    "languageId": self.tab.filetype.name.lower(),
                    "version": self._version,
                    "text": tab_text(self.tab),
                }
            },
        }

        # TODO(PurpleMyst): Initialization takes forever. While a printout
        # is fine for development, we probably should add a little spinny
        # thing somewhere.
        print("Language server for {!r} is initialized.".format(self.tab.path))

        self.tab.bind(
            "<<FiletypeChanged>>", lambda *_: self._on_filetype_changed()
        )

        porcupine.utils.bind_with_data(
            self.tab.textwidget, "<<ContentChanged>>", self._on_content_changed
        )

    def _on_filetype_changed(self) -> None:
        raise RuntimeError("Don't change the filetype!!!")

    def _on_content_changed(self, event):
        self._version += 1

        start, end, range_length, new_text = event.data_tuple(
            str, str, int, str
        )
        start = tk_pos_to_lsp_pos(start)
        end = tk_pos_to_lsp_pos(end)

        self._client.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": tab_uri(self.tab),
                    "version": self._version,
                },
                "contentChanges": [
                    {
                        "text": new_text,
                        "range": {"start": start, "end": end},
                        "rangeLength": range_length,
                    }
                ],
            },
        )

    def _porcufy_completion_item(self, item):
        if "textEdit" in item:
            edit = item["textEdit"]
            edit_range = edit["range"]

            start = lsp_pos_to_tk_pos(edit_range["start"])
            end = lsp_pos_to_tk_pos(edit_range["end"])
            new_text = edit["newText"]
        elif "insertText" in item:
            new_text = item["insertText"]

            line, _ = map(int, self.tab.textwidget.index("insert").split("."))
            start, end = find_overlap_start(
                line,
                self.tab.textwidget.get("insert linestart", "insert"),
                new_text,
            )
        else:
            raise RuntimeError(
                "Completion item %r had neither textEdit nor insertText"
                % (item,)
            )

        return (start, end, new_text)

    # coroutine
    def get_completions(self):
        completion_items = yield {
            "action": "request",
            "method": "textDocument/completion",
            "params": {
                "textDocument": {
                    "uri": tab_uri(self.tab),
                    "version": self._version,
                },
                "position": tab_position(self.tab),
            },
        }
        completions = map(self._porcufy_completion_item, completion_items)

        if hasattr(self.tab, "set_completions"):
            self.tab.set_completions(completions)
        else:
            print("Completions:", list(completions))
