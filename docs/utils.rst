:mod:`porcupine.utils` --- Handy utility functions and classes
==============================================================

.. module:: porcupine.utils

This module contains handy things that Porcupine uses internally and
plugins can use freely.


Information about Python
------------------------

.. data:: running_pythonw

    This is True if Python is running in pythonw.exe on Windows.

    The ``pythonw.exe`` program runs Python scripts without a command
    prompt, so you need to check for that when doing things like
    starting a new command prompt from Python.

.. data:: python_executable

   Like :data:`sys.executable`, but this should also be correct on
   ``pythonw.exe``.


Events with Data
----------------

.. autofunction:: bind_with_data
.. autoclass:: EventWithData
    :members:
.. autoclass:: EventDataclass


Other Tkinter Utilities
-----------------------

See :mod:`porcupine.textutils` for ``tkinter.Text`` specific things.

.. autofunction:: set_tooltip
.. autofunction:: bind_tab_key
.. autofunction:: add_scroll_command
.. autofunction:: run_in_thread
.. autofunction:: errordialog


Miscellaneous
-------------

.. autofunction:: invert_color
.. autofunction:: mix_colors
.. autofunction:: backup_open

.. function:: quote(argument)

   Add quotes around an argument of a command.

   This function is equivalent to :func:`shlex.quote` on non-Windows systems,
   and on Windows it adds double quotes in a similar way. This is useful for
   running commands in the Windows command prompt or a POSIX-compatible shell.
