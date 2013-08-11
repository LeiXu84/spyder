# -*- coding: utf-8 -*-
#
# Copyright © 2009-2010 Pierre Raybaut
# Licensed under the terms of the MIT License
# (see spyderlib/__init__.py for details)

"""
Editor widget based on QtGui.QPlainTextEdit
"""

# pylint: disable=C0103
# pylint: disable=R0903
# pylint: disable=R0911
# pylint: disable=R0201

from __future__ import division

import sys
import os
import re
import sre_constants
import os.path as osp
import time

from spyderlib.qt import is_pyqt46
from spyderlib.qt.QtGui import (QColor, QMenu, QApplication, QSplitter, QFont,
                                QTextEdit, QTextFormat, QPainter, QTextCursor,
                                QBrush, QTextDocument, QTextCharFormat,
                                QPixmap, QPrinter, QToolTip, QCursor, QLabel,
                                QInputDialog, QTextBlockUserData, QLineEdit,
                                QShortcut, QKeySequence, QWidget, QVBoxLayout,
                                QKeyEvent, QHBoxLayout, QDialog, QIntValidator,
                                QDialogButtonBox, QGridLayout)
from spyderlib.qt.QtCore import (Qt, SIGNAL, QTimer, QRect, QRegExp, QSize,
                                 SLOT, Slot)
from spyderlib.qt.compat import to_qvariant

# Local import
#TODO: Try to separate this module from spyderlib to create a self
#      consistent editor module (Qt source code and shell widgets library)
from spyderlib.baseconfig import get_conf_path, _, DEBUG, get_image_path
from spyderlib.config import CONF
from spyderlib.guiconfig import get_font
from spyderlib.utils.qthelpers import (add_actions, create_action, keybinding,
                                       mimedata2url, get_icon)
from spyderlib.utils.dochelpers import getobj
from spyderlib.utils import encoding, sourcecode
from spyderlib.utils.debug import log_last_error, log_dt
from spyderlib.widgets.editortools import PythonCFM
from spyderlib.widgets.sourcecode.base import TextEditBaseWidget
from spyderlib.widgets.sourcecode import syntaxhighlighters as sh

# For debugging purpose:
LOG_FILENAME = get_conf_path('rope.log')


#===============================================================================
# Code introspection features: rope integration
#===============================================================================
try:
    try:
        from spyderlib import rope_patch
        rope_patch.apply()
    except ImportError:
        # rope 0.9.2/0.9.3 is not installed
        pass
    import rope.base.libutils
    import rope.contrib.codeassist
except ImportError:
    pass


#TODO: The following preferences should be customizable in the future
ROPE_PREFS = {'ignore_syntax_errors': True,
              'ignore_bad_imports': True,
              'soa_followed_calls': 2,
              'extension_modules': [],
              }


class RopeProject(object):
    def __init__(self):
        self.project = None
        self.create_rope_project(root_path=get_conf_path())

    #------rope integration
    def create_rope_project(self, root_path):
        try:
            import rope.base.project
            self.project = rope.base.project.Project(
                encoding.to_fs_from_unicode(root_path), **ROPE_PREFS)
        except ImportError:
            self.project = None
            if DEBUG:
                log_last_error(LOG_FILENAME,
                               "create_rope_project: %r" % root_path)
        except TypeError:
            # Compatibility with new Mercurial API (>= 1.3).
            # New versions of rope (> 0.9.2) already handle this issue
            self.project = None
            if DEBUG:
                log_last_error(LOG_FILENAME,
                               "create_rope_project: %r" % root_path)
        self.validate_rope_project()

    def close_rope_project(self):
        if self.project is not None:
            self.project.close()

    def validate_rope_project(self):
        if self.project is not None:
            self.project.validate(self.project.root)

    def set_pref(self, key, value):
        if self.project is not None:
            self.project.prefs.set(key, value)

    def get_completion_list(self, source_code, offset, filename):
        if self.project is None:
            return []
        try:
            resource = rope.base.libutils.path_to_resource(self.project,
                                                   filename.encode('utf-8'))
        except Exception, _error:
            if DEBUG:
                log_last_error(LOG_FILENAME, "path_to_resource: %r" % filename)
            resource = None
        try:
            if DEBUG:
                t0 = time.time()
            proposals = rope.contrib.codeassist.code_assist(self.project,
                                    source_code, offset, resource, maxfixes=3)
            proposals = rope.contrib.codeassist.sorted_proposals(proposals)
            if DEBUG:
                log_dt(LOG_FILENAME, "code_assist/sorted_proposals", t0)
            return [proposal.name for proposal in proposals]
        except Exception, _error:  #analysis:ignore
            if DEBUG:
                log_last_error(LOG_FILENAME, "get_completion_list")
            return []

    def get_calltip_and_docs(self, source_code, offset, filename):
        if self.project is None:
            return []
        try:
            resource = rope.base.libutils.path_to_resource(self.project,
                                                   filename.encode('utf-8'))
        except Exception, _error:
            if DEBUG:
                log_last_error(LOG_FILENAME, "path_to_resource: %r" % filename)
            resource = None
        try:
            if DEBUG:
                t0 = time.time()
            cts = rope.contrib.codeassist.get_calltip(
                            self.project, source_code, offset, resource,
                            ignore_unknown=False, remove_self=True, maxfixes=3)
            if DEBUG:
                log_dt(LOG_FILENAME, "get_calltip", t0)
            if cts is not None:
                while '..' in cts:
                    cts = cts.replace('..', '.')
                if '(.)' in cts:
                    cts = cts.replace('(.)', '(...)')
            try:
                doc_text = rope.contrib.codeassist.get_doc(self.project,
                                     source_code, offset, resource, maxfixes=3)
                if DEBUG:
                    log_dt(LOG_FILENAME, "get_doc", t0)
            except Exception, _error:
                doc_text = ''
                if DEBUG:
                    log_last_error(LOG_FILENAME, "get_doc")
            return [cts, doc_text]
        except Exception, _error:  #analysis:ignore
            if DEBUG:
                log_last_error(LOG_FILENAME, "get_calltip_text")
            return []

    def get_definition_location(self, source_code, offset, filename):
        if self.project is None:
            return (None, None)
        try:
            resource = rope.base.libutils.path_to_resource(self.project,
                                                   filename.encode('utf-8'))
        except Exception, _error:
            if DEBUG:
                log_last_error(LOG_FILENAME, "path_to_resource: %r" % filename)
            resource = None
        try:
            if DEBUG:
                t0 = time.time()
            resource, lineno = rope.contrib.codeassist.get_definition_location(
                    self.project, source_code, offset, resource, maxfixes=3)
            if DEBUG:
                log_dt(LOG_FILENAME, "get_definition_location", t0)
            if resource is not None:
                filename = resource.real_path
            return filename, lineno
        except Exception, _error:  #analysis:ignore
            if DEBUG:
                log_last_error(LOG_FILENAME, "get_definition_location")
            return (None, None)


ROPE_PROJECT = None
def get_rope_project():
    """Create a single rope project"""
    global ROPE_PROJECT
    if ROPE_PROJECT is None:
        ROPE_PROJECT = RopeProject()
    return ROPE_PROJECT

def validate_rope_project():
    """Validate rope project"""
    get_rope_project().validate_rope_project()


#===============================================================================
# Go to line dialog box
#===============================================================================
class GoToLineDialog(QDialog):
    def __init__(self, editor):
        QDialog.__init__(self, editor)
        
        # Destroying the C++ object right after closing the dialog box,
        # otherwise it may be garbage-collected in another QThread
        # (e.g. the editor's analysis thread in Spyder), thus leading to
        # a segmentation fault on UNIX or an application crash on Windows
        self.setAttribute(Qt.WA_DeleteOnClose)
        
        self.lineno = None
        self.editor = editor

        self.setWindowTitle(_("Editor"))
        self.setModal(True)

        label = QLabel(_("Go to line:"))
        self.lineedit = QLineEdit()
        validator = QIntValidator(self.lineedit)
        validator.setRange(1, editor.get_line_count())
        self.lineedit.setValidator(validator)
        self.connect(self.lineedit, SIGNAL('textChanged(QString)'),
                     self.text_has_changed)
        cl_label = QLabel(_("Current line:"))
        cl_label_v = QLabel("<b>%d</b>" % editor.get_cursor_line_number())
        last_label = QLabel(_("Line count:"))
        last_label_v = QLabel("%d" % editor.get_line_count())

        glayout = QGridLayout()
        glayout.addWidget(label, 0, 0, Qt.AlignVCenter|Qt.AlignRight)
        glayout.addWidget(self.lineedit, 0, 1, Qt.AlignVCenter)
        glayout.addWidget(cl_label, 1, 0, Qt.AlignVCenter|Qt.AlignRight)
        glayout.addWidget(cl_label_v, 1, 1, Qt.AlignVCenter)
        glayout.addWidget(last_label, 2, 0, Qt.AlignVCenter|Qt.AlignRight)
        glayout.addWidget(last_label_v, 2, 1, Qt.AlignVCenter)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel,
                                Qt.Vertical, self)
        self.connect(bbox, SIGNAL("accepted()"), SLOT("accept()"))
        self.connect(bbox, SIGNAL("rejected()"), SLOT("reject()"))
        btnlayout = QVBoxLayout()
        btnlayout.addWidget(bbox)
        btnlayout.addStretch(1)

        ok_button = bbox.button(QDialogButtonBox.Ok)
        ok_button.setEnabled(False)
        self.connect(self.lineedit, SIGNAL("textChanged(QString)"),
                     lambda text: ok_button.setEnabled(len(text) > 0))

        layout = QHBoxLayout()
        layout.addLayout(glayout)
        layout.addLayout(btnlayout)
        self.setLayout(layout)

        self.lineedit.setFocus()
        
    def text_has_changed(self, text):
        """Line edit's text has changed"""
        text = unicode(text)
        if text:
            self.lineno = int(text)
        else:
            self.lineno = None

    def get_line_number(self):
        """Return line number"""
        # It is import to avoid accessing Qt C++ object as it has probably
        # already been destroyed, due to the Qt.WA_DeleteOnClose attribute
        return self.lineno


#===============================================================================
# Viewport widgets
#===============================================================================
class LineNumberArea(QWidget):
    """Line number area (on the left side of the text editor widget)"""
    def __init__(self, editor):
        QWidget.__init__(self, editor)
        self.code_editor = editor
        self.setMouseTracking(True)

    def sizeHint(self):
        """Override Qt method"""
        return QSize(self.code_editor.compute_linenumberarea_width(), 0)

    def paintEvent(self, event):
        """Override Qt method"""
        self.code_editor.linenumberarea_paint_event(event)

    def mouseMoveEvent(self, event):
        """Override Qt method"""
        self.code_editor.linenumberarea_mousemove_event(event)

    def mouseDoubleClickEvent(self, event):
        """Override Qt method"""
        self.code_editor.linenumberarea_mousedoubleclick_event(event)


class ScrollFlagArea(QWidget):
    """Source code editor's scroll flag area"""
    WIDTH = 12
    FLAGS_DX = 4
    FLAGS_DY = 2
    
    def __init__(self, editor):
        QWidget.__init__(self, editor)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.code_editor = editor
        self.connect(editor.verticalScrollBar(), SIGNAL('valueChanged(int)'),
                     lambda value: self.repaint())

    def sizeHint(self):
        """Override Qt method"""
        return QSize(self.WIDTH, 0)

    def paintEvent(self, event):
        """Override Qt method"""
        self.code_editor.scrollflagarea_paint_event(event)

    def mousePressEvent(self, event):
        """Override Qt method"""
        vsb = self.code_editor.verticalScrollBar()
        value = self.position_to_value(event.pos().y()-1)
        vsb.setValue(value-.5*vsb.pageStep())
        
    def get_scale_factor(self, slider=False):
        """Return scrollbar's scale factor:
        ratio between pixel span height and value span height"""
        delta = 0 if slider else 2
        vsb = self.code_editor.verticalScrollBar()
        position_height = vsb.height()-delta-1
        value_height = vsb.maximum()-vsb.minimum()+vsb.pageStep()
        return float(position_height)/value_height
        
    def value_to_position(self, y, slider=False):
        """Convert value to position"""
        offset = 0 if slider else 1
        vsb = self.code_editor.verticalScrollBar()
        return (y-vsb.minimum())*self.get_scale_factor(slider)+offset
        
    def position_to_value(self, y, slider=False):
        """Convert position to value"""
        offset = 0 if slider else 1
        vsb = self.code_editor.verticalScrollBar()
        return vsb.minimum()+max([0, (y-offset)/self.get_scale_factor(slider)])

    def make_flag_qrect(self, position):
        """Make flag QRect"""
        return QRect(self.FLAGS_DX/2, position-self.FLAGS_DY/2,
                     self.WIDTH-self.FLAGS_DX, self.FLAGS_DY)

    def make_slider_range(self, value):
        """Make slider range QRect"""
        vsb = self.code_editor.verticalScrollBar()
        pos1 = self.value_to_position(value, slider=True)
        pos2 = self.value_to_position(value + vsb.pageStep(), slider=True)
        return QRect(1, pos1, self.WIDTH-2, pos2-pos1+1)


class EdgeLine(QWidget):
    """Source code editor's edge line (default: 79 columns, PEP8)"""
    def __init__(self, editor):
        QWidget.__init__(self, editor)
        self.code_editor = editor
        self.column = 79

    def paintEvent(self, event):
        """Override Qt method"""
        painter = QPainter(self)
        color = QColor(Qt.darkGray)
        color.setAlphaF(.5)
        painter.fillRect(event.rect(), color)


#===============================================================================
# CodeEditor widget
#===============================================================================
class BlockUserData(QTextBlockUserData):
    def __init__(self, editor):
        QTextBlockUserData.__init__(self)
        self.editor = editor
        self.breakpoint = False
        self.breakpoint_condition = None
        self.code_analysis = []
        self.todo = ''
        self.editor.blockuserdata_list.append(self)

    def is_empty(self):
        return not self.breakpoint and not self.code_analysis and not self.todo

    def __del__(self):
        bud_list = self.editor.blockuserdata_list
        bud_list.pop(bud_list.index(self))


def get_primary_at(source_code, offset):
    """Return Python object in *source_code* at *offset*"""
    try:
        import rope.base.worder
        word_finder = rope.base.worder.Worder(source_code, True)
        return word_finder.get_primary_at(offset)
    except ImportError:
        return


def set_scrollflagarea_painter(painter, light_color):
    """Set scroll flag area painter pen and brush colors"""
    painter.setPen(QColor(light_color).darker(120))
    painter.setBrush(QBrush(QColor(light_color)))


def get_file_language(filename, text=None):
    """Get file language from filename"""
    ext = osp.splitext(filename)[1]
    if ext.startswith('.'):
        ext = ext[1:] # file extension with leading dot
    language = ext
    if not ext:
        if text is None:
            text, _enc = encoding.read(filename)
        for line in text.splitlines():
            if not line.strip():
                continue
            if line.startswith('#!') and \
               line[2:].split() == ['/usr/bin/env', 'python']:
                    language = 'python'
            else:
                break
    return language


class CodeEditor(TextEditBaseWidget):
    """Source Code Editor Widget based exclusively on Qt"""
    LANGUAGES = {
                 ('py', 'pyw', 'python', 'ipy'): (sh.PythonSH, '#', PythonCFM),
                 ('pyx', 'pxi', 'pxd'): (sh.CythonSH, '#', PythonCFM),
                 ('f', 'for', 'f77'): (sh.Fortran77SH, 'c', None),
                 ('f90', 'f95', 'f2k'): (sh.FortranSH, '!', None),
                 ('pro',): (sh.IdlSH, ';', None),
                 ('m',): (sh.MatlabSH, '%', None),
                 ('diff', 'patch', 'rej'): (sh.DiffSH, '', None),
                 ('po', 'pot'): (sh.GetTextSH, '#', None),
                 ('nsi', 'nsh'): (sh.NsisSH, '#', None),
                 ('htm', 'html'): (sh.HtmlSH, '', None),
                 ('css',): (sh.CssSH, '', None),
                 ('xml',): (sh.XmlSH, '', None),
                 ('js',): (sh.JsSH, '', None),
                 ('c', 'cc', 'cpp', 'cxx', 'h', 'hh', 'hpp', 'hxx',
                  ): (sh.CppSH, '//', None),
                 ('cl',): (sh.OpenCLSH, '//', None),
                 ('bat', 'cmd', 'nt'): (sh.BatchSH, 'rem ', None),
                 ('properties', 'session', 'ini', 'inf', 'reg', 'url',
                  'cfg', 'cnf', 'aut', 'iss'): (sh.IniSH, '#', None),
                 }
    try:
        import pygments  # analysis:ignore
    except ImportError:
        # Removing all syntax highlighters requiring pygments to be installed
        for key, (sh_class, comment_string, CFMatch) in LANGUAGES.items():
            if issubclass(sh_class, sh.PygmentsSH):
                LANGUAGES.pop(key)
    
    TAB_ALWAYS_INDENTS = ('py', 'pyw', 'python', 'c', 'cpp', 'cl', 'h')

    def __init__(self, parent=None):
        TextEditBaseWidget.__init__(self, parent)

        # Calltips
        calltip_size = CONF.get('editor_appearance', 'calltips/size')
        calltip_font = get_font('editor_appearance', 'calltips')
        self.setup_calltips(calltip_size, calltip_font)

        # Completion
        completion_size = CONF.get('editor_appearance', 'completion/size')
        completion_font = get_font('editor_appearance', 'completion')
        self.completion_widget.setup_appearance(completion_size,
                                                completion_font)

        # Caret (text cursor)
        self.setCursorWidth( CONF.get('editor_appearance', 'cursor/width') )

        # Side areas background color
        self.area_background_color = QColor(Qt.white)

        # 79-col edge line
        self.edge_line_enabled = True
        self.edge_line = EdgeLine(self)

        # Markers
        self.markers_margin = True
        self.markers_margin_width = 15
        self.error_pixmap = QPixmap(get_image_path('error.png'), 'png')
        self.warning_pixmap = QPixmap(get_image_path('warning.png'), 'png')
        self.todo_pixmap = QPixmap(get_image_path('todo.png'), 'png')
        self.bp_pixmap = QPixmap(get_image_path('breakpoint_small.png'), 'png')
        self.bpc_pixmap = QPixmap(get_image_path('breakpoint_cond_small.png'),
                                                 'png')

        # Line number area management
        self.linenumbers_margin = True
        self.linenumberarea_enabled = None
        self.linenumberarea = LineNumberArea(self)
        self.connect(self, SIGNAL("blockCountChanged(int)"),
                     self.update_linenumberarea_width)
        self.connect(self, SIGNAL("updateRequest(QRect,int)"),
                     self.update_linenumberarea)


        # --- Syntax highlight entrypoint ---
        #
        # - if set, self.highlighter is responsible for
        #   - coloring raw text data inside editor on load
        #   - coloring text data when editor is cloned
        #   - updating document highlight on line edits
        #   - providing color palette (scheme) for the editor
        #   - providing data for Outliner
        # - self.highlighter is not responsible for
        #   - background highlight for current line
        #   - background highlight for search / current line occurences

        self.highlighter_class = sh.TextSH
        self.highlighter = None
        ccs = 'Spyder'
        if ccs not in sh.COLOR_SCHEME_NAMES:
            ccs = sh.COLOR_SCHEME_NAMES[0]
        self.color_scheme = ccs

        #  Background colors: current line, occurences
        self.currentline_color = QColor(Qt.red).lighter(190)
        self.highlight_current_line_enabled = False
        

        # Scrollbar flag area
        self.scrollflagarea_enabled = None
        self.scrollflagarea = ScrollFlagArea(self)
        self.scrollflagarea.hide()
        self.warning_color = "#FFAD07"
        self.error_color = "#EA2B0E"
        self.todo_color = "#B4D4F3"
        self.breakpoint_color = "#30E62E"

        self.update_linenumberarea_width()

        self.document_id = id(self)

        # Indicate occurences of the selected word
        self.connect(self, SIGNAL('cursorPositionChanged()'),
                     self.__cursor_position_changed)
        self.__find_first_pos = None
        self.__find_flags = None

        self.supported_language = None
        self.classfunc_match = None
        self.comment_string = None

        # Block user data
        self.blockuserdata_list = []
        
        # Update breakpoints if the number of lines in the file changes
        self.connect(self, SIGNAL("blockCountChanged(int)"),
                     self.update_breakpoints)

        # Mark occurences timer
        self.occurence_highlighting = None
        self.occurence_timer = QTimer(self)
        self.occurence_timer.setSingleShot(True)
        self.occurence_timer.setInterval(1500)
        self.connect(self.occurence_timer, SIGNAL("timeout()"),
                     self.__mark_occurences)
        self.occurences = []
        self.occurence_color = QColor(Qt.yellow).lighter(160)
        
        # Mark found results
        self.connect(self, SIGNAL('textChanged()'), self.__text_has_changed)
        self.found_results = []
        self.found_results_color = QColor(Qt.magenta).lighter(180)

        # Context menu
        self.gotodef_action = None
        self.setup_context_menu()

        # Tab key behavior
        self.tab_indents = None
        self.tab_mode = True # see CodeEditor.set_tab_mode

        # Intelligent backspace mode
        self.intelligent_backspace = True

        self.go_to_definition_enabled = False
        self.close_parentheses_enabled = True
        self.close_quotes_enabled = False
        self.add_colons_enabled = True
        self.auto_unindent_enabled = True

        # Mouse tracking
        self.setMouseTracking(True)
        self.__cursor_changed = False
        self.ctrl_click_color = QColor(Qt.blue)
        
        # Breakpoints
        self.breakpoints = self.get_breakpoints()

        # Keyboard shortcuts
        ctrl = "Meta" if sys.platform == 'darwin' else "Ctrl"
        self.codecomp_sc = QShortcut(QKeySequence(ctrl+"+Space"), self,
                                     self.do_code_completion)
        self.codecomp_sc.setContext(Qt.WidgetWithChildrenShortcut)
        dup_seq = "Ctrl+Alt+Up" if os.name == 'nt' else "Shift+Alt+Up"
        self.duplicate_sc = QShortcut(QKeySequence(dup_seq), self,
                                      self.duplicate_line)
        self.duplicate_sc.setContext(Qt.WidgetWithChildrenShortcut)
        cop_seq = "Ctrl+Alt+Down" if os.name == 'nt' else "Shift+Alt+Down"
        self.copyline_sc = QShortcut(QKeySequence(cop_seq), self,
                                     self.copy_line)
        self.copyline_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.deleteline_sc = QShortcut(QKeySequence("Ctrl+D"), self,
                                       self.delete_line)
        self.deleteline_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.movelineup_sc = QShortcut(QKeySequence("Alt+Up"), self,
                                       self.move_line_up)
        self.movelineup_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.movelinedown_sc = QShortcut(QKeySequence("Alt+Down"), self,
                                         self.move_line_down)
        self.movelinedown_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.gotodef_sc = QShortcut(QKeySequence("Ctrl+G"), self,
                                    self.do_go_to_definition)
        self.gotodef_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.toggle_comment_sc = QShortcut(QKeySequence("Ctrl+1"), self,
                                           self.toggle_comment)
        self.toggle_comment_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.blockcomment_sc = QShortcut(QKeySequence("Ctrl+4"), self,
                                         self.blockcomment)
        self.blockcomment_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self.unblockcomment_sc = QShortcut(QKeySequence("Ctrl+5"), self,
                                           self.unblockcomment)
        self.unblockcomment_sc.setContext(Qt.WidgetWithChildrenShortcut)

    def get_shortcut_data(self):
        """
        Returns shortcut data, a list of tuples (shortcut, text, default)
        shortcut (QShortcut or QAction instance)
        text (string): action/shortcut description
        default (string): default key sequence
        """
        ctrl = "Meta" if sys.platform == 'darwin' else "Ctrl"
        return [
                (self.codecomp_sc, "Code completion", ctrl+"+Space"),
                (self.duplicate_sc, "Duplicate line", ctrl+"+Alt+Up"),
                (self.copyline_sc, "Copy line", ctrl+"+Alt+Down"),
                (self.movelineup_sc, "Move line up", "Alt+Up"),
                (self.movelinedown_sc, "Move line down", "Alt+Down"),
                (self.deleteline_sc, "Delete line", "Ctrl+D"),
                (self.gotodef_sc, "Go to definition", "Ctrl+G"),
                (self.toggle_comment_sc, "Toggle comment", "Ctrl+1"),
                (self.blockcomment_sc, "Blockcomment", "Ctrl+4"),
                (self.unblockcomment_sc, "Unblockcomment", "Ctrl+5"),
                ]

    def closeEvent(self, event):
        TextEditBaseWidget.closeEvent(self, event)
        if is_pyqt46:
            self.emit(SIGNAL('destroyed()'))


    def get_document_id(self):
        return self.document_id

    def set_as_clone(self, editor):
        """Set as clone editor"""
        self.setDocument(editor.document())
        self.document_id = editor.get_document_id()
        self.highlighter = editor.highlighter
        self._apply_highlighter_color_scheme()

    #-----Widget setup and options
    def toggle_wrap_mode(self, enable):
        """Enable/disable wrap mode"""
        self.set_wrap_mode('word' if enable else None)

    def setup_editor(self, linenumbers=True, language=None, markers=False,
                     font=None, color_scheme=None, wrap=False, tab_mode=True,
                     intelligent_backspace=True, highlight_current_line=True,
                     occurence_highlighting=True, scrollflagarea=True,
                     edge_line=True, edge_line_column=79,
                     codecompletion_auto=False, codecompletion_case=True,
                     codecompletion_single=False, codecompletion_enter=False,
                     calltips=None, go_to_definition=False,
                     close_parentheses=True, close_quotes=False,
                     add_colons=True, auto_unindent=True, indent_chars=" "*4,
                     tab_stop_width=40, cloned_from=None):
        # Code completion and calltips
        self.set_codecompletion_auto(codecompletion_auto)
        self.set_codecompletion_case(codecompletion_case)
        self.set_codecompletion_single(codecompletion_single)
        self.set_codecompletion_enter(codecompletion_enter)
        self.set_calltips(calltips)
        self.set_go_to_definition_enabled(go_to_definition)
        self.set_close_parentheses_enabled(close_parentheses)
        self.set_close_quotes_enabled(close_quotes)
        self.set_add_colons_enabled(add_colons)
        self.set_auto_unindent_enabled(auto_unindent)
        self.set_indent_chars(indent_chars)
        self.setTabStopWidth(tab_stop_width)

        # Scrollbar flag area
        self.set_scrollflagarea_enabled(scrollflagarea)

        # Edge line
        self.set_edge_line_enabled(edge_line)
        self.set_edge_line_column(edge_line_column)

        # Line number area
        if cloned_from:
            self.setFont(font) # this is required for line numbers area
        self.setup_margins(linenumbers, markers)

        # Lexer
        self.set_language(language)

        # Highlight current line
        self.set_highlight_current_line(highlight_current_line)

        # Occurence highlighting
        self.set_occurence_highlighting(occurence_highlighting)

        # Tab always indents (even when cursor is not at the begin of line)
        self.set_tab_mode(tab_mode)

        # Intelligent backspace
        self.toggle_intelligent_backspace(intelligent_backspace)

        if cloned_from is not None:
            self.set_as_clone(cloned_from)
            self.update_linenumberarea_width()
        elif font is not None:
            self.set_font(font, color_scheme)
        elif color_scheme is not None:
            self.set_color_scheme(color_scheme)

        self.toggle_wrap_mode(wrap)

    def set_tab_mode(self, enable):
        """
        enabled = tab always indent
        (otherwise tab indents only when cursor is at the beginning of a line)
        """
        self.tab_mode = enable

    def toggle_intelligent_backspace(self, state):
        self.intelligent_backspace = state

    def set_go_to_definition_enabled(self, enable):
        """Enable/Disable go-to-definition feature, which is implemented in
        child class -> Editor widget"""
        self.go_to_definition_enabled = enable
        self.gotodef_action.setEnabled(enable)

    def set_close_parentheses_enabled(self, enable):
        """Enable/disable automatic parentheses insertion feature"""
        self.close_parentheses_enabled = enable
    
    def set_close_quotes_enabled(self, enable):
        """Enable/disable automatic quote insertion feature"""
        self.close_quotes_enabled = enable
    
    def set_add_colons_enabled(self, enable):
        """Enable/disable automatic colons insertion feature"""
        self.add_colons_enabled = enable

    def set_auto_unindent_enabled(self, enable):
        """Enable/disable automatic unindent after else/elif/finally/except"""
        self.auto_unindent_enabled = enable

    def set_occurence_highlighting(self, enable):
        """Enable/disable occurence highlighting"""
        self.occurence_highlighting = enable
        if not enable:
            self.__clear_occurences()

    def set_occurence_timeout(self, timeout):
        """Set occurence highlighting timeout (ms)"""
        self.occurence_timer.setInterval(timeout)

    def set_highlight_current_line(self, enable):
        """Enable/disable current line highlighting"""
        self.highlight_current_line_enabled = enable
        self.highlight_current_line()

    def set_language(self, language):
        self.tab_indents = language in self.TAB_ALWAYS_INDENTS
        self.supported_language = False
        self.comment_string = ''
        sh_class = sh.TextSH
        if language is not None:
            for key in self.LANGUAGES:
                if language.lower() in key:
                    self.supported_language = True
                    sh_class, comment_string, CFMatch = self.LANGUAGES[key]
                    self.comment_string = comment_string
                    if CFMatch is None:
                        self.classfunc_match = None
                    else:
                        self.classfunc_match = CFMatch()
                    break
        self._set_highlighter(sh_class)

    def _set_highlighter(self, sh_class):
        if self.highlighter_class is not sh_class:
            self.highlighter_class = sh_class
            if self.highlighter is not None:
                # Removing old highlighter
                # TODO: test if leaving parent/document as is eats memory
                self.highlighter.setParent(None)
                self.highlighter.setDocument(None)
            self.highlighter = self.highlighter_class(self.document(),
                                                self.font(), self.color_scheme)
            self._apply_highlighter_color_scheme()

    def is_python(self):
        return self.highlighter_class is sh.PythonSH

    def is_cython(self):
        return self.highlighter_class is sh.CythonSH

    def rehighlight(self):
        """
        Rehighlight the whole document to rebuild outline explorer data
        and import statements data from scratch
        """
        if self.highlighter is not None:
            self.highlighter.rehighlight()
        self.highlight_current_line()


    def setup_margins(self, linenumbers=True, markers=True):
        """
        Setup margin settings
        (except font, now set in self.set_font)
        """
        self.linenumbers_margin = linenumbers
        self.markers_margin = markers
        self.set_linenumberarea_enabled(linenumbers or markers)

    def remove_trailing_spaces(self):
        """Remove trailing spaces"""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.Start)
        while True:
            cursor.movePosition(QTextCursor.EndOfBlock)
            text = unicode(cursor.block().text())
            length = len(text)-len(text.rstrip())
            if length > 0:
                cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor,
                                    length)
                cursor.removeSelectedText()
            if cursor.atEnd():
                break
            cursor.movePosition(QTextCursor.NextBlock)
        cursor.endEditBlock()

    def fix_indentation(self):
        """Replace tabs by spaces"""
        text_before = unicode(self.toPlainText())
        text_after = sourcecode.fix_indentation(text_before)
        if text_before != text_after:
            self.setPlainText(text_after)
            self.document().setModified(True)

    def get_current_object(self):
        """Return current object (string) -- requires 'rope'"""
        source_code = unicode(self.toPlainText())
        offset = self.get_position('cursor')
        return get_primary_at(source_code, offset)

    #------Find occurences
    def __find_first(self, text):
        """Find first occurence: scan whole document"""
        flags = QTextDocument.FindCaseSensitively|QTextDocument.FindWholeWords
        cursor = self.textCursor()
        # Scanning whole document
        cursor.movePosition(QTextCursor.Start)
        regexp = QRegExp(r"\b%s\b" % QRegExp.escape(text), Qt.CaseSensitive)
        cursor = self.document().find(regexp, cursor, flags)
        self.__find_first_pos = cursor.position()
        return cursor

    def __find_next(self, text, cursor):
        """Find next occurence"""
        flags = QTextDocument.FindCaseSensitively|QTextDocument.FindWholeWords
        regexp = QRegExp(r"\b%s\b" % QRegExp.escape(text), Qt.CaseSensitive)
        cursor = self.document().find(regexp, cursor, flags)
        if cursor.position() != self.__find_first_pos:
            return cursor

    def __cursor_position_changed(self):
        """Cursor position has changed"""
        line, column = self.get_cursor_line_column()
        self.emit(SIGNAL('cursorPositionChanged(int,int)'), line, column)
        self.highlight_current_line()
        if self.occurence_highlighting:
            self.occurence_timer.stop()
            self.occurence_timer.start()

    def __clear_occurences(self):
        """Clear occurence markers"""
        self.occurences = []
        self.clear_extra_selections('occurences')
        self.scrollflagarea.update()

    def __highlight_selection(self, key, cursor, foreground_color=None,
                        background_color=None, underline_color=None,
                        underline_style=QTextCharFormat.SpellCheckUnderline,
                        update=False):
        extra_selections = self.get_extra_selections(key)
        selection = QTextEdit.ExtraSelection()
        if foreground_color is not None:
            selection.format.setForeground(foreground_color)
        if background_color is not None:
            selection.format.setBackground(background_color)
        if underline_color is not None:
            selection.format.setProperty(QTextFormat.TextUnderlineStyle,
                                         to_qvariant(underline_style))
            selection.format.setProperty(QTextFormat.TextUnderlineColor,
                                         to_qvariant(underline_color))
        selection.format.setProperty(QTextFormat.FullWidthSelection,
                                     to_qvariant(True))
        selection.cursor = cursor
        extra_selections.append(selection)
        self.set_extra_selections(key, extra_selections)
        if update:
            self.update_extra_selections()

    def __mark_occurences(self):
        """Marking occurences of the currently selected word"""
        self.__clear_occurences()

        if not self.supported_language:
            return
            
        text = self.get_current_word()
        if text is None:
            return
        if self.has_selected_text() and self.get_selected_text() != text:
            return
            
        if (self.is_python() or self.is_cython()) and \
           (sourcecode.is_keyword(unicode(text)) or unicode(text) == 'self'):
            return

        # Highlighting all occurences of word *text*
        cursor = self.__find_first(text)
        self.occurences = []
        while cursor:
            self.occurences.append(cursor.blockNumber())
            self.__highlight_selection('occurences', cursor,
                                       background_color=self.occurence_color)
            cursor = self.__find_next(text, cursor)
        self.update_extra_selections()
        if len(self.occurences) > 1 and self.occurences[-1] == 0:
            # XXX: this is never happening with PySide but it's necessary
            # for PyQt4... this must be related to a different behavior for 
            # the QTextDocument.find function between those two libraries
            self.occurences.pop(-1)
        self.scrollflagarea.update()

    #-----highlight found results (find/replace widget)
    def highlight_found_results(self, pattern, words=False, regexp=False):
        """Highlight all found patterns"""
        pattern = unicode(pattern)
        if not pattern:
            return
        if not regexp:
            pattern = re.escape(unicode(pattern))
        pattern = r"\b%s\b" % pattern if words else pattern
        text = unicode(self.toPlainText())
        try:
            regobj = re.compile(pattern)
        except sre_constants.error:
            return
        extra_selections = []
        self.found_results = []
        for match in regobj.finditer(text):
            pos1, pos2 = match.span()
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(self.found_results_color)
            selection.cursor = self.textCursor()
            selection.cursor.setPosition(pos1)
            self.found_results.append(selection.cursor.blockNumber())
            selection.cursor.setPosition(pos2, QTextCursor.KeepAnchor)
            extra_selections.append(selection)
        self.set_extra_selections('find', extra_selections)
        self.update_extra_selections()
        
    def clear_found_results(self):
        """Clear found results highlighting"""
        self.found_results = []
        self.clear_extra_selections('find')
        self.scrollflagarea.update()
        
    def __text_has_changed(self):
        """Text has changed, eventually clear found results highlighting"""
        if self.found_results:
            self.clear_found_results()

    #-----markers
    def get_markers_margin(self):
        if self.markers_margin:
            return self.markers_margin_width
        else:
            return 0

    #-----linenumberarea
    def set_linenumberarea_enabled(self, state):
        self.linenumberarea_enabled = state
        self.linenumberarea.setVisible(state)
        self.update_linenumberarea_width()

    def get_linenumberarea_width(self):
        """Return current line number area width"""
        return self.linenumberarea.contentsRect().width()

    def compute_linenumberarea_width(self):
        """Compute and return line number area width"""
        if not self.linenumberarea_enabled:
            return 0
        digits = 1
        maxb = max(1, self.blockCount())
        while maxb >= 10:
            maxb /= 10
            digits += 1
        if self.linenumbers_margin:
            linenumbers_margin = 3+self.fontMetrics().width('9'*digits)
        else:
            linenumbers_margin = 0
        return linenumbers_margin+self.get_markers_margin()

    def update_linenumberarea_width(self, new_block_count=None):
        """
        Update line number area width.
        
        new_block_count is needed to handle blockCountChanged(int) signal
        """
        self.setViewportMargins(self.compute_linenumberarea_width(), 0,
                                self.get_scrollflagarea_width(), 0)

    def update_linenumberarea(self, qrect, dy):
        """Update line number area"""
        if dy:
            self.linenumberarea.scroll(0, dy)
        else:
            self.linenumberarea.update(0, qrect.y(),
                                       self.linenumberarea.width(),
                                       qrect.height())
        if qrect.contains(self.viewport().rect()):
            self.update_linenumberarea_width()

    def linenumberarea_paint_event(self, event):
        """Painting line number area"""
        font_height = self.fontMetrics().height()
        painter = QPainter(self.linenumberarea)
        painter.fillRect(event.rect(), self.area_background_color)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(
                                                    self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        painter.setPen(Qt.darkGray)
        def draw_pixmap(ytop, pixmap):
            painter.drawPixmap(0, ytop+(font_height-pixmap.height())/2, pixmap)
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_number = block_number+1
                if self.linenumbers_margin:
                    painter.drawText(0, top, self.linenumberarea.width(),
                                     font_height, Qt.AlignRight|Qt.AlignBottom,
                                     unicode(line_number))
                data = block.userData()
                if self.markers_margin and data:
                    if data.code_analysis:
                        for _message, error in data.code_analysis:
                            if error:
                                break
                        if error:
                            draw_pixmap(top, self.error_pixmap)
                        else:
                            draw_pixmap(top, self.warning_pixmap)
                    if data.todo:
                        draw_pixmap(top, self.todo_pixmap)
                    if data.breakpoint:
                        if data.breakpoint_condition is None:
                            draw_pixmap(top, self.bp_pixmap)
                        else:
                            draw_pixmap(top, self.bpc_pixmap)

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def __get_linenumber_from_mouse_event(self, event):
        """Return line number from mouse event"""
        block = self.firstVisibleBlock()
        line_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(
                                                    self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top < event.pos().y():
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            line_number += 1

        return line_number

    def linenumberarea_mousemove_event(self, event):
        """Handling line number area mouse move event"""
        line_number = self.__get_linenumber_from_mouse_event(event)
        block = self.document().findBlockByNumber(line_number-1)
        data = block.userData()
        if data and data.code_analysis:
            self.__show_code_analysis_results(line_number, data.code_analysis)

    def linenumberarea_mousedoubleclick_event(self, event):
        """Handling line number area mouse double-click event"""
        line_number = self.__get_linenumber_from_mouse_event(event)
        shift = event.modifiers() & Qt.ShiftModifier
        self.add_remove_breakpoint(line_number, edit_condition=shift)


    #------Breakpoints
    def add_remove_breakpoint(self, line_number=None, condition=None,
                              edit_condition=False):
        """Add/remove breakpoint"""
        if not self.is_python() and not self.is_cython():
            return
        if line_number is None:
            block = self.textCursor().block()
        else:
            block = self.document().findBlockByNumber(line_number-1)
        data = block.userData()
        if data:
            data.breakpoint = not data.breakpoint
            data.breakpoint_condition = None
        else:
            data = BlockUserData(self)
            data.breakpoint = True
        if condition is not None:
            data.breakpoint_condition = condition
        if edit_condition:
            data.breakpoint = True
            condition = data.breakpoint_condition
            if condition is None:
                condition = ''
            condition, valid = QInputDialog.getText(self,
                                        _('Breakpoint'),
                                        _("Condition:"),
                                        QLineEdit.Normal, condition)
            if valid:
                condition = str(condition)
                if not condition:
                    condition = None
                data.breakpoint_condition = condition
            else:
                return
        if data.breakpoint:
            text = unicode(block.text()).strip()
            if len(text) == 0 or text.startswith('#') or text.startswith('"') \
               or text.startswith("'"):
                data.breakpoint = False
        block.setUserData(data)
        self.linenumberarea.update()
        self.scrollflagarea.update()
        self.emit(SIGNAL('breakpoints_changed()'))

    def get_breakpoints(self):
        """Get breakpoints"""
        breakpoints = []
        block = self.document().firstBlock()
        for line_number in xrange(1, self.document().blockCount()+1):
            data = block.userData()
            if data and data.breakpoint:
                breakpoints.append((line_number, data.breakpoint_condition))
            block = block.next()
        return breakpoints

    def clear_breakpoints(self):
        """Clear breakpoints"""
        self.breakpoints = []
        for data in self.blockuserdata_list[:]:
            data.breakpoint = False
#            data.breakpoint_condition = None # not necessary, but logical
            if data.is_empty():
                del data

    def set_breakpoints(self, breakpoints):
        """Set breakpoints"""
        self.clear_breakpoints()
        for line_number, condition in breakpoints:
            self.add_remove_breakpoint(line_number, condition)

    def update_breakpoints(self):
        """Update breakpoints"""
        self.emit(SIGNAL('breakpoints_changed()'))

    #-----Code introspection
    def do_code_completion(self):
        """Trigger code completion"""
        if not self.is_completion_widget_visible():
            self.emit(SIGNAL('trigger_code_completion(bool)'), False)

    def do_go_to_definition(self):
        """Trigger go-to-definition"""
        self.emit(SIGNAL("go_to_definition(int)"), self.textCursor().position())


    #-----edge line
    def set_edge_line_enabled(self, state):
        """Toggle edge line visibility"""
        self.edge_line_enabled = state
        self.edge_line.setVisible(state)

    def set_edge_line_column(self, column):
        """Set edge line column value"""
        self.edge_line.column = column
        self.edge_line.update()


    #-----scrollflagarea
    def set_scrollflagarea_enabled(self, state):
        """Toggle scroll flag area visibility"""
        self.scrollflagarea_enabled = state
        self.scrollflagarea.setVisible(state)
        self.update_linenumberarea_width()

    def get_scrollflagarea_width(self):
        """Return scroll flag area width"""
        if self.scrollflagarea_enabled:
            return ScrollFlagArea.WIDTH
        else:
            return 0

    def scrollflagarea_paint_event(self, event):
        """Painting the scroll flag area"""
        make_flag = self.scrollflagarea.make_flag_qrect
        make_slider = self.scrollflagarea.make_slider_range
        
        # Filling the whole painting area
        painter = QPainter(self.scrollflagarea)
        painter.fillRect(event.rect(), self.area_background_color)
        block = self.document().firstBlock()
        
        # Painting warnings and todos
        for line_number in xrange(1, self.document().blockCount()+1):
            data = block.userData()
            if data:
                position = self.scrollflagarea.value_to_position(line_number)
                if data.code_analysis:
                    # Warnings
                    color = self.warning_color
                    for _message, error in data.code_analysis:
                        if error:
                            color = self.error_color
                            break
                    set_scrollflagarea_painter(painter, color)
                    painter.drawRect(make_flag(position))
                if data.todo:
                    # TODOs
                    set_scrollflagarea_painter(painter, self.todo_color)
                    painter.drawRect(make_flag(position))
                if data.breakpoint:
                    # Breakpoints
                    set_scrollflagarea_painter(painter, self.breakpoint_color)
                    painter.drawRect(make_flag(position))
            block = block.next()
            
        # Occurences
        if self.occurences:
            set_scrollflagarea_painter(painter, self.occurence_color)
            for line_number in self.occurences:
                position = self.scrollflagarea.value_to_position(line_number)
                painter.drawRect(make_flag(position))
            
        # Found results
        if self.found_results:
            set_scrollflagarea_painter(painter, self.found_results_color)
            for line_number in self.found_results:
                position = self.scrollflagarea.value_to_position(line_number)
                painter.drawRect(make_flag(position))

        # Painting the slider range
        pen_color = QColor(Qt.white)
        pen_color.setAlphaF(.8)
        painter.setPen(pen_color)
        brush_color = QColor(Qt.white)
        brush_color.setAlphaF(.5)
        painter.setBrush(QBrush(brush_color))
        painter.drawRect(make_slider(self.firstVisibleBlock().blockNumber()))

    def resizeEvent(self, event):
        """Reimplemented Qt method to handle line number area resizing"""
        TextEditBaseWidget.resizeEvent(self, event)
        cr = self.contentsRect()
        self.linenumberarea.setGeometry(\
                        QRect(cr.left(), cr.top(),
                              self.compute_linenumberarea_width(), cr.height()))
        self.__set_scrollflagarea_geometry(cr)

    def __set_scrollflagarea_geometry(self, contentrect):
        """Set scroll flag area geometry"""
        cr = contentrect
        if self.verticalScrollBar().isVisible():
            vsbw = self.verticalScrollBar().contentsRect().width()
        else:
            vsbw = 0
        _left, _top, right, _bottom = self.getContentsMargins()
        if right > vsbw:
            # Depending on the platform (e.g. on Ubuntu), the scrollbar sizes
            # may be taken into account in the contents margins whereas it is
            # not on Windows for example
            vsbw = 0
        self.scrollflagarea.setGeometry(\
                        QRect(cr.right()-ScrollFlagArea.WIDTH-vsbw, cr.top(),
                              self.scrollflagarea.WIDTH, cr.height()))

    #-----edgeline
    def viewportEvent(self, event):
        """Override Qt method"""
        # 79-column edge line
        cr = self.contentsRect()
        offset = self.contentOffset()
        x = self.blockBoundingGeometry(self.firstVisibleBlock()) \
            .translated(offset.x(), offset.y()).left() \
            +self.get_linenumberarea_width() \
            +self.fontMetrics().width('9'*self.edge_line.column)+5
        self.edge_line.setGeometry(\
                        QRect(x, cr.top(), 1, cr.bottom()))
        self.__set_scrollflagarea_geometry(cr)
        return TextEditBaseWidget.viewportEvent(self, event)

    #-----highlight current line
    def highlight_current_line(self):
        """Highlight current line. Works without self.highlighter"""
        if self.highlight_current_line_enabled:
            selection = QTextEdit.ExtraSelection()
            selection.format.setProperty(QTextFormat.FullWidthSelection,
                                         to_qvariant(True))
            selection.format.setBackground(self.currentline_color)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            self.set_extra_selections('current_line', [selection])
            self.update_extra_selections()
        else:
            self.clear_extra_selections('current_line')


    def delete(self):
        """Remove selected text"""
        # Used by global callbacks in Spyder -> delete_action
        self.remove_selected_text()

    def _apply_highlighter_color_scheme(self):
        """Apply color scheme from syntax highlighter to the editor"""
        hl = self.highlighter
        if hl is not None:
            self.set_palette(background=hl.get_background_color(),
                             foreground=hl.get_foreground_color())
            self.currentline_color = hl.get_currentline_color()
            self.occurence_color = hl.get_occurence_color()
            self.ctrl_click_color = hl.get_ctrlclick_color()
            self.area_background_color = hl.get_sideareas_color()
            self.matched_p_color = hl.get_matched_p_color()
            self.unmatched_p_color = hl.get_unmatched_p_color()

    def apply_highlighter_settings(self, color_scheme=None):
        """Apply syntax highlighter settings"""
        if self.highlighter is not None:
            # Updating highlighter settings (font and color scheme)
            self.highlighter.setup_formats(self.font())
            if color_scheme is not None:
                self.set_color_scheme(color_scheme)
            else:
                self.highlighter.rehighlight()

    def set_font(self, font, color_scheme=None):
        """Set shell font"""
        # Note: why using this method to set color scheme instead of
        #       'set_color_scheme'? To avoid rehighlighting the document twice
        #       at startup.
        if color_scheme is not None:
            self.color_scheme = color_scheme
        self.setFont(font)
        self.update_linenumberarea_width()
        self.apply_highlighter_settings(color_scheme)

    def set_color_scheme(self, color_scheme):
        """Set color scheme for syntax highlighting"""
        self.color_scheme = color_scheme
        if self.highlighter is not None:
            # this calls self.highlighter.rehighlight()
            self.highlighter.set_color_scheme(color_scheme)
            self._apply_highlighter_color_scheme()
        self.highlight_current_line()

    def set_text(self, text):
        """Set the text of the editor"""
        self.setPlainText(text)
        self.set_eol_chars(text)
#        if self.supported_language:
#            self.highlighter.rehighlight()

    def set_text_from_file(self, filename, language=None):
        """Set the text of the editor from file *fname*"""
        text, _enc = encoding.read(filename)
        if language is None:
            language = get_file_language(filename, text)
        self.set_language(language)
        self.set_text(text)

    def append(self, text):
        """Append text to the end of the text widget"""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)

    def paste(self):
        """
        Reimplement QPlainTextEdit's method to fix the following issue:
        on Windows, pasted text has only 'LF' EOL chars even if the original
        text has 'CRLF' EOL chars
        """
        clipboard = QApplication.clipboard()
        text = unicode(clipboard.text())
        if len(text.splitlines()) > 1:
            eol_chars = self.get_line_separator()
            clipboard.setText( eol_chars.join((text+eol_chars).splitlines()) )
        # Standard paste
        TextEditBaseWidget.paste(self)

    def get_block_data(self, block):
        """Return block data (from syntax highlighter)"""
        return self.highlighter.block_data.get(block)

    def get_fold_level(self, block_nb):
        """Is it a fold header line?
        If so, return fold level
        If not, return None"""
        block = self.document().findBlockByNumber(block_nb)
        return self.get_block_data(block).fold_level


#===============================================================================
#    High-level editor features
#===============================================================================
    @Slot()
    def center_cursor_on_next_focus(self):
        """QPlainTextEdit's "centerCursor" requires the widget to be visible"""
        self.centerCursor()
        self.disconnect(self, SIGNAL("focus_in()"),
                        self.center_cursor_on_next_focus)

    def go_to_line(self, line, word=''):
        """Go to line number *line* and eventually highlight it"""
        block = self.document().findBlockByNumber(line-1)
        self.setTextCursor(QTextCursor(block))
        if self.isVisible():
            self.centerCursor()
        else:
            self.connect(self, SIGNAL("focus_in()"),
                         self.center_cursor_on_next_focus)
        self.horizontalScrollBar().setValue(0)
        if word and unicode(word) in unicode(block.text()):
            self.find(word, QTextDocument.FindCaseSensitively)

    def exec_gotolinedialog(self):
        """Execute the GoToLineDialog dialog box"""
        dlg = GoToLineDialog(self)
        if dlg.exec_():
            self.go_to_line(dlg.get_line_number())

    def cleanup_code_analysis(self):
        """Remove all code analysis markers"""
        self.setUpdatesEnabled(False)
        self.clear_extra_selections('code_analysis')
        for data in self.blockuserdata_list[:]:
            data.code_analysis = []
            if data.is_empty():
                del data
        self.setUpdatesEnabled(True)
        # When the new code analysis results are empty, it is necessary
        # to update manually the scrollflag and linenumber areas (otherwise, 
        # the old flags will still be displayed):
        self.scrollflagarea.update()
        self.linenumberarea.update()

    def process_code_analysis(self, check_results):
        """Analyze filename code with pyflakes"""
        self.cleanup_code_analysis()
        if check_results is None:
            # Not able to compile module
            return
        self.setUpdatesEnabled(False)
        cursor = self.textCursor()
        document = self.document()
        flags = QTextDocument.FindCaseSensitively|QTextDocument.FindWholeWords
        for message, line_number in check_results:
            error = 'syntax' in message
            # Note: line_number start from 1 (not 0)
            block = self.document().findBlockByNumber(line_number-1)
            data = block.userData()
            if not data:
                data = BlockUserData(self)
            data.code_analysis.append( (message, error) )
            block.setUserData(data)
            refs = re.findall(r"\'[a-zA-Z0-9_]*\'", message)
            for ref in refs:
                # Highlighting found references
                text = ref[1:-1]
                # Scanning line number *line* and following lines if continued
                def is_line_splitted(line_no):
                    text = unicode(document.findBlockByNumber(line_no).text())
                    stripped = text.strip()
                    return stripped.endswith('\\') or stripped.endswith(',') \
                           or len(stripped) == 0
                line2 = line_number-1
                while line2 < self.blockCount()-1 and is_line_splitted(line2):
                    line2 += 1
                cursor.setPosition(block.position())
                cursor.movePosition(QTextCursor.StartOfBlock)
                regexp = QRegExp(r"\b%s\b" % QRegExp.escape(text),
                                 Qt.CaseSensitive)
                color = self.error_color if error else self.warning_color
                # Highlighting all occurences (this is a compromise as pyflakes
                # do not provide the column number -- see Issue 709 on Spyder's
                # GoogleCode project website)
                cursor = document.find(regexp, cursor, flags)
                if cursor:
                    while cursor and cursor.blockNumber() <= line2 \
                          and cursor.blockNumber() >= line_number-1 \
                          and cursor.position() > 0:
                        self.__highlight_selection('code_analysis', cursor,
                                       underline_color=QColor(color))
                        cursor = document.find(text, cursor, flags)
        self.update_extra_selections()
        self.setUpdatesEnabled(True)
        self.linenumberarea.update()

    def __show_code_analysis_results(self, line_number, code_analysis):
        """Show warning/error messages"""
        msglist = [ msg for msg, _error in code_analysis ]
        self.show_calltip(_("Code analysis"), msglist,
                          color='#129625', at_line=line_number)

    def go_to_next_warning(self):
        """Go to next code analysis warning message
        and return new cursor position"""
        block = self.textCursor().block()
        line_count = self.document().blockCount()
        while True:
            if block.blockNumber()+1 < line_count:
                block = block.next()
            else:
                block = self.document().firstBlock()
            data = block.userData()
            if data and data.code_analysis:
                break
        line_number = block.blockNumber()+1
        self.go_to_line(line_number)
        self.__show_code_analysis_results(line_number, data.code_analysis)
        return self.get_position('cursor')

    def go_to_previous_warning(self):
        """Go to previous code analysis warning message
        and return new cursor position"""
        block = self.textCursor().block()
        while True:
            if block.blockNumber() > 0:
                block = block.previous()
            else:
                block = self.document().lastBlock()
            data = block.userData()
            if data and data.code_analysis:
                break
        line_number = block.blockNumber()+1
        self.go_to_line(line_number)
        self.__show_code_analysis_results(line_number, data.code_analysis)
        return self.get_position('cursor')


    #------Tasks management
    def go_to_next_todo(self):
        """Go to next todo and return new cursor position"""
        block = self.textCursor().block()
        line_count = self.document().blockCount()
        while True:
            if block.blockNumber()+1 < line_count:
                block = block.next()
            else:
                block = self.document().firstBlock()
            data = block.userData()
            if data and data.todo:
                break
        line_number = block.blockNumber()+1
        self.go_to_line(line_number)
        self.show_calltip(_("To do"), data.todo,
                          color='#3096FC', at_line=line_number)
        return self.get_position('cursor')

    def process_todo(self, todo_results):
        """Process todo finder results"""
        for data in self.blockuserdata_list[:]:
            data.todo = ''
            if data.is_empty():
                del data
        for message, line_number in todo_results:
            block = self.document().findBlockByNumber(line_number-1)
            data = block.userData()
            if not data:
                data = BlockUserData(self)
            data.todo = message
            block.setUserData(data)
        self.scrollflagarea.update()


    #------Comments/Indentation
    def add_prefix(self, prefix):
        """Add prefix to current line or selected line(s)"""
        cursor = self.textCursor()
        if self.has_selected_text():
            # Add prefix to selected line(s)
            start_pos, end_pos = cursor.selectionStart(), cursor.selectionEnd()

            # Let's see if selection begins at a block start
            first_pos = min([start_pos, end_pos])
            first_cursor = self.textCursor()
            first_cursor.setPosition(first_pos)
            begins_at_block_start = first_cursor.atBlockStart()

            cursor.beginEditBlock()
            cursor.setPosition(end_pos)
            # Check if end_pos is at the start of a block: if so, starting
            # changes from the previous block
            if cursor.atBlockStart():
                cursor.movePosition(QTextCursor.PreviousBlock)
                if cursor.position() < start_pos:
                    cursor.setPosition(start_pos)

            while cursor.position() >= start_pos:
                cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.insertText(prefix)
                if start_pos == 0 and cursor.blockNumber() == 0:
                    # Avoid infinite loop when indenting the very first line
                    break
                cursor.movePosition(QTextCursor.PreviousBlock)
                cursor.movePosition(QTextCursor.EndOfBlock)
            cursor.endEditBlock()
            if begins_at_block_start:
                # Extending selection to prefix:
                cursor = self.textCursor()
                start_pos = cursor.selectionStart()
                end_pos = cursor.selectionEnd()
                if start_pos < end_pos:
                    start_pos -= len(prefix)
                else:
                    end_pos -= len(prefix)
                cursor.setPosition(start_pos, QTextCursor.MoveAnchor)
                cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
        else:
            # Add prefix to current line
            cursor.beginEditBlock()
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.insertText(prefix)
            cursor.endEditBlock()

    def __is_cursor_at_start_of_block(self, cursor):
        cursor.movePosition(QTextCursor.StartOfBlock)


    def remove_suffix(self, suffix):
        """
        Remove suffix from current line (there should not be any selection)
        """
        cursor = self.textCursor()
        cursor.setPosition(cursor.position()-len(suffix),
                           QTextCursor.KeepAnchor)
        if unicode(cursor.selectedText()) == suffix:
            cursor.removeSelectedText()

    def remove_prefix(self, prefix):
        """Remove prefix from current line or selected line(s)"""
        cursor = self.textCursor()
        if self.has_selected_text():
            # Remove prefix from selected line(s)
            start_pos, end_pos = sorted([cursor.selectionStart(),
                                         cursor.selectionEnd()])
            cursor.setPosition(start_pos)
            if not cursor.atBlockStart():
                cursor.movePosition(QTextCursor.StartOfBlock)
                start_pos = cursor.position()
            cursor.beginEditBlock()
            cursor.setPosition(end_pos)
            # Check if end_pos is at the start of a block: if so, starting
            # changes from the previous block
            if cursor.atBlockStart():
                cursor.movePosition(QTextCursor.PreviousBlock)
                if cursor.position() < start_pos:
                    cursor.setPosition(start_pos)

            cursor.movePosition(QTextCursor.StartOfBlock)
            old_pos = None
            while cursor.position() >= start_pos:
                new_pos = cursor.position()
                if old_pos == new_pos:
                    break
                else:
                    old_pos = new_pos
                line_text = unicode(cursor.block().text())
                if (prefix.strip() and line_text.lstrip().startswith(prefix)
                    or line_text.startswith(prefix)):
                    cursor.movePosition(QTextCursor.Right,
                                        QTextCursor.MoveAnchor,
                                        line_text.find(prefix))
                    cursor.movePosition(QTextCursor.Right,
                                        QTextCursor.KeepAnchor, len(prefix))
                    cursor.removeSelectedText()
                cursor.movePosition(QTextCursor.PreviousBlock)
            cursor.endEditBlock()
        else:
            # Remove prefix from current line
            cursor.movePosition(QTextCursor.StartOfBlock)
            line_text = unicode(cursor.block().text())
            if (prefix.strip() and line_text.lstrip().startswith(prefix)
                or line_text.startswith(prefix)):
                cursor.movePosition(QTextCursor.Right,
                                    QTextCursor.MoveAnchor,
                                    line_text.find(prefix))
                cursor.movePosition(QTextCursor.Right,
                                    QTextCursor.KeepAnchor, len(prefix))
                cursor.removeSelectedText()

    def fix_indent(self, forward=True, comment_or_string=False):
        """
        Fix indentation (Python only, no text selection)
        forward=True: fix indent only if text is not enough indented
                      (otherwise force indent)
        forward=False: fix indent only if text is too much indented
                       (otherwise force unindent)

        Returns True if indent needed to be fixed
        """
        if not self.is_python() and not self.is_cython():
            return
        cursor = self.textCursor()
        block_nb = cursor.blockNumber()
        for prevline in xrange(block_nb-1, -1, -1):
            cursor.movePosition(QTextCursor.PreviousBlock)
            prevtext = unicode(cursor.block().text()).rstrip()
            if not prevtext.strip().startswith('#'):
                break
        indent = self.get_block_indentation(block_nb)
        correct_indent = self.get_block_indentation(prevline)

        if not comment_or_string:
            if prevtext.endswith(':'):
                # Indent
                correct_indent += len(self.indent_chars)
            elif prevtext.endswith('continue') or prevtext.endswith('break') \
              or prevtext.endswith('pass'):
                # Unindent
                correct_indent -= len(self.indent_chars)
            elif prevtext.endswith(',') \
              and len(re.split(r'\(|\{|\[', prevtext)) > 1:
                rlmap = {")":"(", "]":"[", "}":"{"}
                for par in rlmap:
                    i_right = prevtext.rfind(par)
                    if i_right != -1:
                        prevtext = prevtext[:i_right]
                        for _i in range(len(prevtext.split(par))):
                            i_left = prevtext.rfind(rlmap[par])
                            if i_left != -1:
                                prevtext = prevtext[:i_left]
                            else:
                                break
                else:
                    prevexpr = re.split(r'\(|\{|\[', prevtext)[-1]
                    correct_indent = len(prevtext)-len(prevexpr)

        if (forward and indent >= correct_indent) or \
           (not forward and indent <= correct_indent):
            # No indentation fix is necessary
            return False

        if correct_indent >= 0:
            cursor = self.textCursor()
            cursor.beginEditBlock()
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.setPosition(cursor.position()+indent, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertText(self.indent_chars[0]*correct_indent)
            cursor.endEditBlock()
            return True

    def indent(self, force=False):
        """
        Indent current line or selection

        force=True: indent even if cursor is not a the beginning of the line
        """
        leading_text = self.get_text('sol', 'cursor')
        if self.has_selected_text():
            self.add_prefix(self.indent_chars)
        elif force or not leading_text.strip() \
             or (self.tab_indents and self.tab_mode):
            if self.is_python() or self.is_cython():
                if not self.fix_indent(forward=True):
                    self.add_prefix(self.indent_chars)
            else:
                self.add_prefix(self.indent_chars)
        else:
            if len(self.indent_chars) > 1:
                length = len(self.indent_chars)
                self.insert_text(" "*(length-(len(leading_text) % length)))
            else:
                self.insert_text(self.indent_chars)

    def indent_or_replace(self):
        """Indent or replace by 4 spaces depending on selection and tab mode"""
        if (self.tab_indents and self.tab_mode) or not self.has_selected_text():
            self.indent()
        else:
            cursor = self.textCursor()
            if self.get_selected_text() == unicode(cursor.block().text()):
                self.indent()
            else:
                cursor1 = self.textCursor()
                cursor1.setPosition(cursor.selectionStart())
                cursor2 = self.textCursor()
                cursor2.setPosition(cursor.selectionEnd())
                if cursor1.blockNumber() != cursor2.blockNumber():
                    self.indent()
                else:
                    self.replace(self.indent_chars)

    def unindent(self, force=False):
        """
        Unindent current line or selection

        force=True: unindent even if cursor is not a the beginning of the line
        """
        if self.has_selected_text():
            self.remove_prefix(self.indent_chars)
        else:
            leading_text = self.get_text('sol', 'cursor')
            if force or not leading_text.strip() \
               or (self.tab_indents and self.tab_mode):
                if self.is_python() or self.is_cython():
                    if not self.fix_indent(forward=False):
                        self.remove_prefix(self.indent_chars)
                elif leading_text.endswith('\t'):
                    self.remove_prefix('\t')
                else:
                    self.remove_prefix(self.indent_chars)

    def toggle_comment(self):
        """Toggle comment on current line or selection"""
        cursor = self.textCursor()
        start_pos, end_pos = sorted([cursor.selectionStart(),
                                     cursor.selectionEnd()])
        cursor.setPosition(end_pos)
        last_line = cursor.block().blockNumber()
        if cursor.atBlockStart() and start_pos != end_pos:
            last_line -= 1
        cursor.setPosition(start_pos)
        first_line = cursor.block().blockNumber()
        # If the selection contains only commented lines and surrounding
        # whitespace, uncomment. Otherwise, comment.
        is_comment_or_whitespace = True
        at_least_one_comment = False
        for _line_nb in range(first_line, last_line+1):
            text = unicode(cursor.block().text()).lstrip()
            is_comment = text.startswith(self.comment_string)
            is_whitespace = (text == '')
            is_comment_or_whitespace *= (is_comment or is_whitespace)
            if is_comment:
                at_least_one_comment = True
            cursor.movePosition(QTextCursor.NextBlock)
        if is_comment_or_whitespace and at_least_one_comment:
            self.uncomment()
        else:
            self.comment()

    def comment(self):
        """Comment current line or selection"""
        self.add_prefix(self.comment_string)

    def uncomment(self):
        """Uncomment current line or selection"""
        self.remove_prefix(self.comment_string)

    def __blockcomment_bar(self):
        return self.comment_string + '='*(79-len(self.comment_string))

    def blockcomment(self):
        """Block comment current line or selection"""
        comline = self.__blockcomment_bar() + self.get_line_separator()
        cursor = self.textCursor()
        if self.has_selected_text():
            self.extend_selection_to_complete_lines()
            start_pos, end_pos = cursor.selectionStart(), cursor.selectionEnd()
            cursor.setPosition(start_pos)
        else:
            start_pos = end_pos = cursor.position()
        cursor.beginEditBlock()
        cursor.setPosition(start_pos)
        cursor.movePosition(QTextCursor.StartOfBlock)
        while cursor.position() <= end_pos:
            cursor.insertText("# ")
            cursor.movePosition(QTextCursor.EndOfBlock)
            if cursor.atEnd():
                break
            cursor.movePosition(QTextCursor.NextBlock)
        cursor.setPosition(end_pos)
        cursor.movePosition(QTextCursor.EndOfBlock)
        if cursor.atEnd():
            cursor.insertText(self.get_line_separator())
        else:
            cursor.movePosition(QTextCursor.NextBlock)
        cursor.insertText(comline)
        cursor.setPosition(start_pos)
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.insertText(comline)
        cursor.endEditBlock()

    def unblockcomment(self):
        """Un-block comment current line or selection"""
        def __is_comment_bar(cursor):
            return unicode(cursor.block().text()
                           ).startswith(self.__blockcomment_bar())
        # Finding first comment bar
        cursor1 = self.textCursor()
        if __is_comment_bar(cursor1):
            return
        while not __is_comment_bar(cursor1):
            cursor1.movePosition(QTextCursor.PreviousBlock)
            if cursor1.atStart():
                break
        if not __is_comment_bar(cursor1):
            return
        def __in_block_comment(cursor):
            return unicode(cursor.block().text()).startswith('#')
        # Finding second comment bar
        cursor2 = QTextCursor(cursor1)
        cursor2.movePosition(QTextCursor.NextBlock)
        while not __is_comment_bar(cursor2) and __in_block_comment(cursor2):
            cursor2.movePosition(QTextCursor.NextBlock)
            if cursor2.block() == self.document().lastBlock():
                break
        if not __is_comment_bar(cursor2):
            return
        # Removing block comment
        cursor3 = self.textCursor()
        cursor3.beginEditBlock()
        cursor3.setPosition(cursor1.position())
        cursor3.movePosition(QTextCursor.NextBlock)
        while cursor3.position() < cursor2.position():
            cursor3.setPosition(cursor3.position()+2, QTextCursor.KeepAnchor)
            cursor3.removeSelectedText()
            cursor3.movePosition(QTextCursor.NextBlock)
        for cursor in (cursor2, cursor1):
            cursor3.setPosition(cursor.position())
            cursor3.select(QTextCursor.BlockUnderCursor)
            cursor3.removeSelectedText()
        cursor3.endEditBlock()
    
    #------Autoinsertion of quotes/colons
    def __get_current_color(self):
        """Get the syntax highlighting color for the current cursor position"""
        cursor = self.textCursor()
        block = cursor.block()
        pos = cursor.position() - block.position()  # relative pos within block
        layout = block.layout()
        block_formats = layout.additionalFormats()
        
        if block_formats:
            # To easily grab current format for autoinsert_colons
            if cursor.atBlockEnd():
                current_format = block_formats[-1].format
            else:
                current_format = None
                for fmt in block_formats:
                    if (pos >= fmt.start) and (pos < fmt.start + fmt.length):
                        current_format = fmt.format
            color = current_format.foreground().color().name()
            return color
        else:
            return None
    
    def in_comment_or_string(self):
        """Is the cursor inside or next to a comment or string?"""
        if self.highlighter:
            current_color = self.__get_current_color()
            comment_color = self.highlighter.get_color_name('comment')
            string_color = self.highlighter.get_color_name('string')
            if (current_color == comment_color) or (current_color == string_color):
                return True
            else:
                return False
        else:
            return False

    def __colon_keyword(self, text):
        stmt_kws = ['def', 'for', 'if', 'while', 'with', 'class', 'elif',
                    'except']
        whole_kws = ['else', 'try', 'except', 'finally']
        text = text.lstrip()
        words = text.split()
        if any([text == wk for wk in whole_kws]):
            return True
        elif len(words) < 2:
            return False
        elif any([words[0] == sk for sk in stmt_kws]):
            return True
        else:
            return False

    def __forbidden_colon_end_char(self, text):
        end_chars = [':', '\\', '[', '{', '(', ',']
        text = text.rstrip()
        if any([text.endswith(c) for c in end_chars]):
            return True
        else:
            return False

    def __unmatched_braces_in_line(self, text):
        block = self.textCursor().block()
        line_pos = block.position()
        for pos,char in enumerate(text):
            if char in ['(', '[', '{']:
                match = self.find_brace_match(line_pos+pos, char, forward=True)
                if (match is None) or (match > line_pos+len(text)):
                    return True
        return False

    def autoinsert_colons(self):
        """Decide if we want to autoinsert colons"""
        line_text = self.get_text('sol', 'cursor')
        if not self.textCursor().atBlockEnd():
            return False
        elif self.in_comment_or_string():
            return False
        elif not self.__colon_keyword(line_text):
            return False
        elif self.__forbidden_colon_end_char(line_text):
            return False
        elif self.__unmatched_braces_in_line(line_text):
            return False
        else:
            return True
    
    def __unmatched_quotes_in_line(self, text):
        """Return whether a string has open quotes.
        This simply counts whether the number of quote characters of either
        type in the string is odd.
        
        Take from the IPython project (in IPython/core/completer.py in v0.13)
        Spyder team: Add some changes to deal with escaped quotes
        
        - Copyright (C) 2008-2011 IPython Development Team
        - Copyright (C) 2001-2007 Fernando Perez. <fperez@colorado.edu>
        - Copyright (C) 2001 Python Software Foundation, www.python.org

        Distributed under the terms of the BSD License.
        """
        # We check " first, then ', so complex cases with nested quotes will
        # get the " to take precedence.
        text = text.replace("\\'", "")
        text = text.replace('\\"', '')
        if text.count('"') % 2:
            return '"'
        elif text.count("'") % 2:
            return "'"
        else:
            return ''

    def __next_char(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.NextCharacter, 
                            QTextCursor.KeepAnchor)
        next_char = unicode(cursor.selectedText())
        return next_char

    def __in_comment(self):
        if self.highlighter:
            current_color = self.__get_current_color()
            comment_color = self.highlighter.get_color_name('comment')
            if current_color == comment_color:
                return True
            else:
                return False
        else:
            return False

    def autoinsert_quotes(self, key):
        """Control how to automatically insert quotes in various situations"""
        char = {Qt.Key_QuoteDbl: '"', Qt.Key_Apostrophe: '\''}[key]
        
        line_text = self.get_text('sol', 'eol')
        line_to_cursor = self.get_text('sol', 'cursor')
        cursor = self.textCursor()
        last_three = self.get_text('sol', 'cursor')[-3:]
        last_two = self.get_text('sol', 'cursor')[-2:]
        trailing_text = self.get_text('cursor', 'eol').strip()

        if self.has_selected_text():
            text = ''.join([char, self.get_selected_text(), char])
            self.insert_text(text)
        elif self.__in_comment():
            self.insert_text(char)
        elif len(trailing_text) > 0 and not \
          self.__unmatched_quotes_in_line(line_to_cursor) == char:
            self.insert_text(char)
        elif self.__unmatched_quotes_in_line(line_text) and \
          (not last_three == 3*char):
            self.insert_text(char)
        # Move to the right if we are before a quote
        elif self.__next_char() == char:
            cursor.movePosition(QTextCursor.NextCharacter,
                                QTextCursor.KeepAnchor, 1)
            cursor.clearSelection()
            self.setTextCursor(cursor)
        # Automatic insertion of triple double quotes (for docstrings)
        elif last_three == 3*char:
            self.insert_text(3*char)
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.PreviousCharacter,
                                QTextCursor.KeepAnchor, 3)
            cursor.clearSelection()
            self.setTextCursor(cursor)
        # If last two chars are quotes, just insert one more because most
        # probably the user wants to write a docstring
        elif last_two == 2*char:
            self.insert_text(char)
        # Automatic insertion of quotes
        else:
            self.insert_text(2*char)
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.PreviousCharacter)
            self.setTextCursor(cursor)

#===============================================================================
#    Qt Event handlers
#===============================================================================
    def setup_context_menu(self):
        """Setup context menu"""
        self.undo_action = create_action(self, _("Undo"),
                           shortcut=keybinding('Undo'),
                           icon=get_icon('undo.png'), triggered=self.undo)
        self.redo_action = create_action(self, _("Redo"),
                           shortcut=keybinding('Redo'),
                           icon=get_icon('redo.png'), triggered=self.redo)
        self.cut_action = create_action(self, _("Cut"),
                           shortcut=keybinding('Cut'),
                           icon=get_icon('editcut.png'), triggered=self.cut)
        self.copy_action = create_action(self, _("Copy"),
                           shortcut=keybinding('Copy'),
                           icon=get_icon('editcopy.png'), triggered=self.copy)
        paste_action = create_action(self, _("Paste"),
                           shortcut=keybinding('Paste'),
                           icon=get_icon('editpaste.png'), triggered=self.paste)
        self.delete_action = create_action(self, _("Delete"),
                           shortcut=keybinding('Delete'),
                           icon=get_icon('editdelete.png'),
                           triggered=self.delete)
        selectall_action = create_action(self, _("Select All"),
                           shortcut=keybinding('SelectAll'),
                           icon=get_icon('selectall.png'),
                           triggered=self.selectAll)
        toggle_comment_action = create_action(self,
                           _("Comment")+"/"+_("Uncomment"),
                           icon=get_icon("comment.png"),
                           triggered=self.toggle_comment)
        self.gotodef_action = create_action(self, _("Go to definition"),
                                   triggered=self.go_to_definition_from_cursor)
        run_selected_action = create_action(self,
                                        _("Run &selection or current block"),
                                        icon='run_selection.png',
                                        triggered=lambda: self.emit(
                                           SIGNAL('triggers_run_selection()')))
        zoom_in_action = create_action(self, _("Zoom in"),
                      QKeySequence(QKeySequence.ZoomIn), icon='zoom_in.png',
                      triggered=lambda: self.emit(SIGNAL('zoom_in()')))
        zoom_out_action = create_action(self, _("Zoom out"),
                      QKeySequence(QKeySequence.ZoomOut), icon='zoom_out.png',
                      triggered=lambda: self.emit(SIGNAL('zoom_out()')))
        self.menu = QMenu(self)
        add_actions(self.menu, (self.undo_action, self.redo_action, None,
                                self.cut_action, self.copy_action,
                                paste_action, self.delete_action,
                                None, selectall_action, None, zoom_in_action,
                                zoom_out_action, None, toggle_comment_action,
                                None, run_selected_action,
                                self.gotodef_action))
            
        # Read-only context-menu
        self.readonly_menu = QMenu(self)
        add_actions(self.readonly_menu,
                    (self.copy_action, None, selectall_action))
    
    def keyPressEvent(self, event):
        """Reimplement Qt method"""
        key = event.key()
        ctrl = event.modifiers() & Qt.ControlModifier
        shift = event.modifiers() & Qt.ShiftModifier
        text = unicode(event.text())
        if text:
            self.__clear_occurences()
        if QToolTip.isVisible():
            self.hide_tooltip_if_necessary(key)
        if key in (Qt.Key_Enter, Qt.Key_Return):
            if not shift and not ctrl:
                if self.add_colons_enabled and self.is_python() and \
                  self.autoinsert_colons():
                    self.insert_text(':' + self.get_line_separator())
                    self.fix_indent()
                elif self.is_completion_widget_visible() \
                   and self.codecompletion_enter:
                    self.select_completion_list()
                else:
                    cmt_or_str = self.in_comment_or_string()
                    TextEditBaseWidget.keyPressEvent(self, event)
                    self.fix_indent(comment_or_string=cmt_or_str)
            elif shift:
                # Ignoring QPlainTextEdit default Shift+Enter keybinding
                # which will print a new line in the same block:
                event = QKeyEvent(event.type(), event.key(), Qt.NoModifier,
                                  event.text(), event.isAutoRepeat(),
                                  event.count())
                TextEditBaseWidget.keyPressEvent(self, event)
                return
        elif key == Qt.Key_Insert and not shift and not ctrl:
            self.setOverwriteMode(not self.overwriteMode())
        elif key == Qt.Key_Backspace and not shift and not ctrl:
            leading_text = self.get_text('sol', 'cursor')
            leading_length = len(leading_text)
            trailing_spaces = leading_length-len(leading_text.rstrip())
            if self.has_selected_text() or not self.intelligent_backspace:
                TextEditBaseWidget.keyPressEvent(self, event)
            else:
                trailing_text = self.get_text('cursor', 'eol')
                if not leading_text.strip() \
                   and leading_length > len(self.indent_chars):
                    if leading_length % len(self.indent_chars) == 0:
                        self.unindent()
                    else:
                        TextEditBaseWidget.keyPressEvent(self, event)
                elif trailing_spaces and not trailing_text.strip():
                    self.remove_suffix(leading_text[-trailing_spaces:])
                elif leading_text and trailing_text and \
                     leading_text[-1]+trailing_text[0] in ('()', '[]', '{}',
                                                           '\'\'', '""'):
                    cursor = self.textCursor()
                    cursor.movePosition(QTextCursor.PreviousCharacter)
                    cursor.movePosition(QTextCursor.NextCharacter,
                                        QTextCursor.KeepAnchor, 2)
                    cursor.removeSelectedText()
                else:
                    TextEditBaseWidget.keyPressEvent(self, event)
                    if self.is_completion_widget_visible():
                        self.completion_text = self.completion_text[:-1]
        elif key == Qt.Key_Period:
            self.insert_text(text)
            if (self.is_python() or self.is_cython()) and not \
              self.in_comment_or_string() and self.codecompletion_auto:
                # Enable auto-completion only if last token isn't a float
                last_obj = getobj(self.get_text('sol', 'cursor'))
                if last_obj and not last_obj.isdigit():
                    self.emit(SIGNAL('trigger_code_completion(bool)'), True)
        elif key == Qt.Key_Home:
            self.stdkey_home(shift, ctrl)
        elif key == Qt.Key_End:
            # See Issue 495: on MacOS X, it is necessary to redefine this 
            # basic action which should have been implemented natively
            self.stdkey_end(shift, ctrl)
        elif text == '(' and not self.has_selected_text():
            self.hide_completion_widget()
            position = self.get_position('cursor')
            s_trailing_text = self.get_text('cursor', 'eol').strip()
            if self.close_parentheses_enabled and \
               (len(s_trailing_text) == 0 or \
                s_trailing_text[0] in (',', ')', ']', '}')):
                self.insert_text('()')
                cursor = self.textCursor()
                cursor.movePosition(QTextCursor.PreviousCharacter)
                self.setTextCursor(cursor)
            else:
                self.insert_text(text)
            if (self.is_python() or self.is_cython()) and \
               self.get_text('sol', 'cursor') and self.calltips:
                self.emit(SIGNAL('trigger_calltip_and_doc_rendering(int)'),
                          position)
        elif text in ('[', '{') and not self.has_selected_text() \
          and self.close_parentheses_enabled:
            s_trailing_text = self.get_text('cursor', 'eol').strip()
            if len(s_trailing_text) == 0 or \
               s_trailing_text[0] in (',', ')', ']', '}'):
                self.insert_text({'{': '{}', '[': '[]'}[text])
                cursor = self.textCursor()
                cursor.movePosition(QTextCursor.PreviousCharacter)
                self.setTextCursor(cursor)
            else:
                TextEditBaseWidget.keyPressEvent(self, event)
        elif key in (Qt.Key_QuoteDbl, Qt.Key_Apostrophe) and \
          self.close_quotes_enabled:
            self.autoinsert_quotes(key)
        elif key in (Qt.Key_ParenRight, Qt.Key_BraceRight, Qt.Key_BracketRight)\
          and not self.has_selected_text() and self.close_parentheses_enabled \
          and not self.textCursor().atBlockEnd():
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.NextCharacter,
                                QTextCursor.KeepAnchor)
            text = unicode(cursor.selectedText())
            if text == {Qt.Key_ParenRight: ')', Qt.Key_BraceRight: '}',
                        Qt.Key_BracketRight: ']'}[key]:
                cursor.clearSelection()
                self.setTextCursor(cursor)
            else:
                TextEditBaseWidget.keyPressEvent(self, event)
        elif key == Qt.Key_Colon and not self.has_selected_text() \
             and self.auto_unindent_enabled:
            leading_text = self.get_text('sol', 'cursor')
            if leading_text.lstrip() in ('else', 'finally'):
                ind = lambda txt: len(txt)-len(txt.lstrip())
                prevtxt = unicode(self.textCursor().block().previous().text())
                if ind(leading_text) == ind(prevtxt):
                    self.unindent(force=True)
            TextEditBaseWidget.keyPressEvent(self, event)
        elif key == Qt.Key_Space and not shift and not ctrl \
             and not self.has_selected_text() and self.auto_unindent_enabled:
            leading_text = self.get_text('sol', 'cursor')
            if leading_text.lstrip() in ('elif', 'except'):
                ind = lambda txt: len(txt)-len(txt.lstrip())
                prevtxt = unicode(self.textCursor().block().previous().text())
                if ind(leading_text) == ind(prevtxt):
                    self.unindent(force=True)
            TextEditBaseWidget.keyPressEvent(self, event)
        elif key == Qt.Key_Tab:
            # Important note: <TAB> can't be called with a QShortcut because
            # of its singular role with respect to widget focus management
            self.indent_or_replace()
        elif key == Qt.Key_Backtab:
            # Backtab, i.e. Shift+<TAB>, could be treated as a QShortcut but
            # there is no point since <TAB> can't (see above)
            self.unindent()
        else:
            TextEditBaseWidget.keyPressEvent(self, event)
            if self.is_completion_widget_visible() and text:
                self.completion_text += text

    def mouseMoveEvent(self, event):
        """Underline words when pressing <CONTROL>"""
        if self.has_selected_text():
            TextEditBaseWidget.mouseMoveEvent(self, event)
            return
        if self.go_to_definition_enabled and \
           event.modifiers() & Qt.ControlModifier:
            text = self.get_word_at(event.pos())
            if text and (self.is_python() or self.is_cython())\
               and not sourcecode.is_keyword(unicode(text)):
                if not self.__cursor_changed:
                    QApplication.setOverrideCursor(
                                                QCursor(Qt.PointingHandCursor))
                    self.__cursor_changed = True
                cursor = self.cursorForPosition(event.pos())
                cursor.select(QTextCursor.WordUnderCursor)
                self.clear_extra_selections('ctrl_click')
                self.__highlight_selection('ctrl_click', cursor, update=True,
                                foreground_color=self.ctrl_click_color,
                                underline_color=self.ctrl_click_color,
                                underline_style=QTextCharFormat.SingleUnderline)
                event.accept()
                return
        if self.__cursor_changed:
            QApplication.restoreOverrideCursor()
            self.__cursor_changed = False
            self.clear_extra_selections('ctrl_click')
        TextEditBaseWidget.mouseMoveEvent(self, event)

    def leaveEvent(self, event):
        """If cursor has not been restored yet, do it now"""
        if self.__cursor_changed:
            QApplication.restoreOverrideCursor()
            self.__cursor_changed = False
            self.clear_extra_selections('ctrl_click')
        TextEditBaseWidget.leaveEvent(self, event)
        
    def go_to_definition_from_cursor(self, cursor=None):
        """Go to definition from cursor instance (QTextCursor)"""
        if cursor is None:
            cursor = self.textCursor()
        position = cursor.position()
        text = unicode(cursor.selectedText())
        if len(text) == 0:
            cursor.select(QTextCursor.WordUnderCursor)
            text = unicode(cursor.selectedText())
        if self.go_to_definition_enabled and text is not None\
           and (self.is_python() or self.is_cython())\
           and not sourcecode.is_keyword(text):
            self.emit(SIGNAL("go_to_definition(int)"), position)

    def mousePressEvent(self, event):
        """Reimplement Qt method"""
        if event.button() == Qt.LeftButton\
           and (event.modifiers() & Qt.ControlModifier):
            TextEditBaseWidget.mousePressEvent(self, event)
            cursor = self.cursorForPosition(event.pos())
            self.go_to_definition_from_cursor(cursor)
        else:
            TextEditBaseWidget.mousePressEvent(self, event)

    def contextMenuEvent(self, event):
        """Reimplement Qt method"""
        state = self.has_selected_text()
        self.copy_action.setEnabled(state)
        self.cut_action.setEnabled(state)
        self.delete_action.setEnabled(state)
        self.undo_action.setEnabled( self.document().isUndoAvailable() )
        self.redo_action.setEnabled( self.document().isRedoAvailable() )
        menu = self.menu
        if self.isReadOnly():
            menu = self.readonly_menu
        menu.popup(event.globalPos())
        event.accept()

    #------ Drag and drop
    def dragEnterEvent(self, event):
        """Reimplement Qt method
        Inform Qt about the types of data that the widget accepts"""
        if mimedata2url(event.mimeData()):
            # Let the parent widget handle this
            event.ignore()
        else:
            TextEditBaseWidget.dragEnterEvent(self, event)

    def dropEvent(self, event):
        """Reimplement Qt method
        Unpack dropped data and handle it"""
        if mimedata2url(event.mimeData()):
            # Let the parent widget handle this
            event.ignore()
        else:
            TextEditBaseWidget.dropEvent(self, event)


#===============================================================================
# CodeEditor's Printer
#===============================================================================

#TODO: Implement the header and footer support
class Printer(QPrinter):
    def __init__(self, mode=QPrinter.ScreenResolution, header_font=None):
        QPrinter.__init__(self, mode)
        self.setColorMode(QPrinter.Color)
        self.setPageOrder(QPrinter.FirstPageFirst)
        self.date = time.ctime()
        if header_font is not None:
            self.header_font = header_font

    # <!> The following method is simply ignored by QPlainTextEdit
    #     (this is a copy from QsciEditor's Printer)
    def formatPage(self, painter, drawing, area, pagenr):
        header = '%s - %s - Page %s' % (self.docName(), self.date, pagenr)
        painter.save()
        painter.setFont(self.header_font)
        painter.setPen(QColor(Qt.black))
        if drawing:
            painter.drawText(area.right()-painter.fontMetrics().width(header),
                             area.top()+painter.fontMetrics().ascent(), header)
        area.setTop(area.top()+painter.fontMetrics().height()+5)
        painter.restore()


#===============================================================================
# Editor + Class browser test
#===============================================================================
class TestWidget(QSplitter):
    def __init__(self, parent):
        QSplitter.__init__(self, parent)
        self.editor = CodeEditor(self)
        self.editor.setup_editor(linenumbers=True, markers=True, tab_mode=False,
                                 font=QFont("Courier New", 10),
                                 color_scheme='Pydev')
        self.addWidget(self.editor)
        from spyderlib.widgets.editortools import OutlineExplorerWidget
        self.classtree = OutlineExplorerWidget(self)
        self.addWidget(self.classtree)
        self.connect(self.classtree, SIGNAL("edit_goto(QString,int,QString)"),
                     lambda _fn, line, word: self.editor.go_to_line(line, word))
        self.setStretchFactor(0, 4)
        self.setStretchFactor(1, 1)
        self.setWindowIcon(get_icon('spyder.svg'))

    def load(self, filename):
        self.editor.set_text_from_file(filename)
        self.setWindowTitle("%s - %s (%s)" % (_("Editor"),
                                              osp.basename(filename),
                                              osp.dirname(filename)))
        self.classtree.set_current_editor(self.editor, filename, False, False)

def test(fname):
    from spyderlib.utils.qthelpers import qapplication
    app = qapplication()
    app.setStyle('Plastique')
    win = TestWidget(None)
    win.show()
    win.load(fname)
    win.resize(1000, 800)

    from spyderlib.utils.codeanalysis import (check_with_pyflakes,
                                              check_with_pep8)
    source_code = unicode(win.editor.toPlainText()).encode('utf-8')
    res = check_with_pyflakes(source_code, fname)#+\
#          check_with_pep8(source_code, fname)
    win.editor.process_code_analysis(res)

    sys.exit(app.exec_())

if __name__ == '__main__':
    if len(sys.argv) > 1:
        fname = sys.argv[1]
    else:
        fname = __file__
#        fname = r"d:\Python\scintilla\src\LexCPP.cxx"
#        fname = r"C:\Python26\Lib\pdb.py"
#        fname = r"C:\Python26\Lib\ssl.py"
#        fname = r"D:\Python\testouille.py"
#        fname = r"C:\Python26\Lib\pydoc.py"
    test(fname)