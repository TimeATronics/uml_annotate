import sys
import json
import copy
import re
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QTextEdit, QLabel, QFileDialog, QMessageBox,
                             QAction, QToolBar, QProgressDialog) # Added QProgressDialog
from PyQt5.QtGui import QTextCharFormat, QColor, QTextCursor, QTextOption
from PyQt5.QtCore import Qt, pyqtSignal

try:
    import spacy
    from spacy.matcher import Matcher
    NLP_PREANNOTATE = None
    MATCHER_PREANNOTATE = None
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    NLP_PREANNOTATE = None
    MATCHER_PREANNOTATE = None
    print("Warning: spaCy library not found. Pre-annotation feature will be disabled.")


class TextSelector(QTextEdit):
    wordSelected = pyqtSignal(str, int, int)
    backgroundClicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self.setReadOnly(False)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setMaximumBlockCount(1000000)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            cursor.select(QTextCursor.WordUnderCursor)

            if cursor.hasSelection():
                word = cursor.selectedText()
                start = cursor.selectionStart()
                end = cursor.selectionEnd()
                self.wordSelected.emit(word, start, end)
            else:
                if self.rect().contains(event.pos()):
                    doc_layout = self.document().documentLayout()
                    fragment_index = doc_layout.hitTest(event.pos(), Qt.ExactHit)
                    if fragment_index == -1:
                        self.backgroundClicked.emit()
        super().mousePressEvent(event)

class AnnotationTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.annotations = {"classes": [], "relationships": []}
        self.highlighted_spans = {}
        self.relationship_mode = False
        self.current_relationship = ""
        self.relationship_from_class_span = None
        self.relationship_to_class_span = None

        self.tag_colors = {
            "CLASS": QColor(255, 182, 193),
            "ATTRIBUTE": QColor(144, 238, 144),
            "METHOD": QColor(173, 216, 230),
            "association": QColor(255, 255, 224),
            "generalization": QColor(221, 160, 221),
            "composition": QColor(175, 238, 238),
            "CONTEXT_CLASS": QColor(255, 165, 0, 150)
        }
        self.current_tag = None
        self.selected_context_class_span = None
        self.undo_stack = []
        self.redo_stack = []
        self._is_processing_json_change = False
        self.nlp_model_preannotate = None # Will hold the loaded spaCy model instance
        # self.matcher_preannotate = None # Matcher instance stored in global for now

        self.initUI()
        self.update_undo_redo_actions()

    def initUI(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)
        self.undo_action = QAction("Undo", self)
        self.undo_action.triggered.connect(self.undo_annotation)
        self.undo_action.setShortcut(Qt.CTRL + Qt.Key_Z)
        toolbar.addAction(self.undo_action)
        self.redo_action = QAction("Redo", self)
        self.redo_action.triggered.connect(self.redo_annotation)
        self.redo_action.setShortcut(Qt.CTRL + Qt.Key_Y)
        toolbar.addAction(self.redo_action)

        text_header_layout = QHBoxLayout()
        self.text_label = QLabel("Text to annotate:")
        text_header_layout.addWidget(self.text_label)

        self.load_text_btn = QPushButton("Load Text File")
        self.load_text_btn.clicked.connect(self.load_text)
        text_header_layout.addWidget(self.load_text_btn)
        
        self.preannotate_btn = QPushButton("Pre-annotate Entities")
        self.preannotate_btn.clicked.connect(self.run_preannotation_pipeline)
        if not SPACY_AVAILABLE:
            self.preannotate_btn.setEnabled(False)
            self.preannotate_btn.setToolTip("spaCy library not found. Install it for pre-annotation.")
        text_header_layout.addWidget(self.preannotate_btn)

        self.clear_text_btn = QPushButton("Clear Text & Annotations")
        self.clear_text_btn.clicked.connect(self.clear_all)
        text_header_layout.addWidget(self.clear_text_btn)
        main_layout.addLayout(text_header_layout)

        self.text_editor = TextSelector()
        self.text_editor.setMinimumHeight(300)
        self.text_editor.wordSelected.connect(self.handle_word_selection)
        self.text_editor.backgroundClicked.connect(self.clear_context_class_selection_from_click)
        main_layout.addWidget(self.text_editor)

        # ... (rest of UI setup: tag buttons, rel buttons, status_label, results_editor) ...
        tag_layout = QHBoxLayout()
        self.class_btn = QPushButton("CLASS")
        self.class_btn.clicked.connect(lambda: self.set_tag_mode("CLASS"))
        self.class_btn.setCheckable(True)
        tag_layout.addWidget(self.class_btn)
        self.attribute_btn = QPushButton("ATTRIBUTE")
        self.attribute_btn.clicked.connect(lambda: self.set_tag_mode("ATTRIBUTE"))
        self.attribute_btn.setCheckable(True)
        tag_layout.addWidget(self.attribute_btn)
        self.method_btn = QPushButton("METHOD")
        self.method_btn.clicked.connect(lambda: self.set_tag_mode("METHOD"))
        self.method_btn.setCheckable(True)
        tag_layout.addWidget(self.method_btn)
        main_layout.addLayout(tag_layout)

        rel_layout = QHBoxLayout()
        self.association_btn = QPushButton("ASSOCIATION")
        self.association_btn.clicked.connect(lambda: self.set_relationship_mode("association"))
        self.association_btn.setCheckable(True)
        rel_layout.addWidget(self.association_btn)
        self.generalization_btn = QPushButton("GENERALIZATION")
        self.generalization_btn.clicked.connect(lambda: self.set_relationship_mode("generalization"))
        self.generalization_btn.setCheckable(True)
        rel_layout.addWidget(self.generalization_btn)
        self.composition_btn = QPushButton("COMPOSITION")
        self.composition_btn.clicked.connect(lambda: self.set_relationship_mode("composition"))
        self.composition_btn.setCheckable(True)
        rel_layout.addWidget(self.composition_btn)
        main_layout.addLayout(rel_layout)

        self.status_label = QLabel("Load text or select a tag, then click on a word.")
        main_layout.addWidget(self.status_label)

        results_header_layout = QHBoxLayout()
        results_label = QLabel("Annotation Results (Editable JSON - Experimental Sync):")
        results_header_layout.addWidget(results_label)
        self.export_btn = QPushButton("Export JSON")
        self.export_btn.clicked.connect(self.export_json)
        results_header_layout.addWidget(self.export_btn)
        self.clear_annotations_btn = QPushButton("Clear Only Annotations")
        self.clear_annotations_btn.clicked.connect(self.clear_annotations_action)
        results_header_layout.addWidget(self.clear_annotations_btn)
        main_layout.addLayout(results_header_layout)

        self.results_editor = QTextEdit()
        self.results_editor.setMinimumHeight(200)
        self.results_editor.setReadOnly(False)
        self.results_editor.textChanged.connect(self.handle_json_text_change)
        main_layout.addWidget(self.results_editor)


        self.setGeometry(100, 100, 900, 800)
        self.setWindowTitle('Enhanced Code Document Annotation Tool')
        self.show()
        sample_text = "The LibrarySystem manages Book items and User accounts. A User can borrow a Book. Each Book has a title and an author. The LibrarySystem provides a searchBook method and a checkoutBook function."
        self.text_editor.setText(sample_text)
        self.push_state_to_undo_stack(initial_state=True)

    def _reset_tag_buttons(self):
        self.class_btn.setChecked(False)
        self.attribute_btn.setChecked(False)
        self.method_btn.setChecked(False)
        self.association_btn.setChecked(False)
        self.generalization_btn.setChecked(False)
        self.composition_btn.setChecked(False)

    def set_tag_mode(self, tag):
        self.push_state_to_undo_stack()
        self._reset_tag_buttons()
        if tag == "CLASS": self.class_btn.setChecked(True)
        elif tag == "ATTRIBUTE": self.attribute_btn.setChecked(True)
        elif tag == "METHOD": self.method_btn.setChecked(True)

        self.current_tag = tag
        self.relationship_mode = False
        self.relationship_from_class_span = None
        if self.selected_context_class_span and tag in ["ATTRIBUTE", "METHOD"]:
            context_class_info = self._get_class_by_span(self.selected_context_class_span)
            if context_class_info:
                self.status_label.setText(f"Context: {context_class_info['name']}. Tag: {tag}. Click word for attribute/method.")
            else:
                self.status_label.setText(f"Selected tag: {tag}. Click on a word.")
        else:
            self.clear_context_class_selection()
            self.status_label.setText(f"Selected tag: {tag}. Click on a word.")
        self.update_undo_redo_actions()

    def set_relationship_mode(self, relationship_type):
        self.push_state_to_undo_stack()
        self._reset_tag_buttons()
        if relationship_type == "association": self.association_btn.setChecked(True)
        elif relationship_type == "generalization": self.generalization_btn.setChecked(True)
        elif relationship_type == "composition": self.composition_btn.setChecked(True)

        self.relationship_mode = True
        self.current_relationship = relationship_type
        self.current_tag = None
        self.relationship_from_class_span = None
        self.clear_context_class_selection()
        self.status_label.setText(f"Rel: {relationship_type}. Click 1st class (FROM), then 2nd (TO).")
        self.update_undo_redo_actions()

    def _get_class_by_span(self, span_tuple):
        if not span_tuple: return None
        for cls in self.annotations["classes"]:
            if cls.get("span") == span_tuple:
                return cls
        return None

    def _get_highlighted_entity_at_pos(self, pos):
        for span, data in sorted(self.highlighted_spans.items(), key=lambda item: item[0][1] - item[0][0], reverse=True):
            if span[0] <= pos < span[1]:
                return span, data
        return None, None
    
    def clear_context_class_selection_from_click(self):
        self.clear_context_class_selection(from_click=True)

    def clear_context_class_selection(self, from_click=False):
        if self.selected_context_class_span:
            original_tag_info = self.highlighted_spans.get(self.selected_context_class_span)
            if original_tag_info and original_tag_info["tag"] == "CLASS":
                self._apply_highlight(self.selected_context_class_span[0], self.selected_context_class_span[1], "CLASS")
            self.selected_context_class_span = None
            if not from_click :
                 self.status_label.setText("Context class cleared. Select tag or action.")
            elif self.current_tag is None and not self.relationship_mode:
                 self.status_label.setText("Context cleared. Select a tag, or a class to set context.")

    def handle_word_selection(self, word, start, end):
        self.push_state_to_undo_stack()
        highlighted_entity_span, highlighted_entity_data = self._get_highlighted_entity_at_pos(start)

        if highlighted_entity_data and highlighted_entity_data["tag"] == "CLASS" and \
           not self.current_tag and not self.relationship_mode:
            self.clear_context_class_selection()
            self.selected_context_class_span = highlighted_entity_span
            self._apply_highlight(start, end, "CONTEXT_CLASS", temporary=True)
            self.status_label.setText(f"Context: {highlighted_entity_data['text']}. Select ATTRIBUTE, METHOD, or a relationship.")
            self.update_results() # update_results takes no args
            self.update_undo_redo_actions()
            return

        if self.current_tag and not self.relationship_mode:
            if self.current_tag in ["ATTRIBUTE", "METHOD"] and not self.selected_context_class_span:
                QMessageBox.warning(self, "Context Needed", f"Please select a parent CLASS first before tagging an {self.current_tag}.")
                self._reset_tag_buttons()
                self.current_tag = None
                self.status_label.setText(f"Action cancelled. Select CLASS for context or another tag.")
                self.pop_state_from_undo_stack()
                return
            self.add_entity(word, start, end, self.current_tag)
        elif self.relationship_mode:
            if not highlighted_entity_data or highlighted_entity_data["tag"] != "CLASS":
                self.status_label.setText(f"'{word}' is not a tagged CLASS. Select valid CLASS for relationship.")
                self.pop_state_from_undo_stack()
                return
            class_span = highlighted_entity_span
            if not self.relationship_from_class_span:
                self.relationship_from_class_span = class_span
                from_class_name = self._get_class_by_span(class_span)["name"]
                self.status_label.setText(f"Rel: {self.current_relationship}. From: {from_class_name}. Select 2nd class (TO).")
            else:
                if class_span == self.relationship_from_class_span:
                    from_class_name = self._get_class_by_span(self.relationship_from_class_span)["name"]
                    self.status_label.setText(f"Cannot relate class to itself. From: {from_class_name}. Select different 2nd class (TO).")
                    self.pop_state_from_undo_stack()
                    return
                self.relationship_to_class_span = class_span
                from_class_obj = self._get_class_by_span(self.relationship_from_class_span)
                to_class_obj = self._get_class_by_span(self.relationship_to_class_span)
                if not from_class_obj or not to_class_obj:
                    QMessageBox.critical(self, "Error", "Could not find class objects for relationship.")
                    self.pop_state_from_undo_stack()
                    return
                self.add_relationship(from_class_obj["name"], to_class_obj["name"], self.current_relationship,
                                      self.relationship_from_class_span, self.relationship_to_class_span)
                self.status_label.setText(f"Added: {from_class_obj['name']} --[{self.current_relationship}]--> {to_class_obj['name']}. Select next 'FROM' or new relationship.")
                self.relationship_from_class_span = None
                self.relationship_to_class_span = None
        else:
            self.pop_state_from_undo_stack()
        self.update_results()
        self.update_undo_redo_actions()

    def _apply_highlight(self, start, end, tag_type, temporary=False):
        cursor = self.text_editor.textCursor()
        cursor.setPosition(start)
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, end - start)
        fmt = QTextCharFormat()
        fmt.setBackground(self.tag_colors[tag_type])
        cursor.mergeCharFormat(fmt)

    def highlight_word(self, word, start, end, tag_type, class_context_name=None):
        self._apply_highlight(start, end, tag_type)
        span_data = {"text": word, "tag": tag_type}
        if class_context_name and tag_type in ["ATTRIBUTE", "METHOD"]:
            span_data["class_name"] = class_context_name
        self.highlighted_spans[(start, end)] = span_data

    def add_entity(self, word, start, end, tag_type):
        entity_span = (start, end)
        if entity_span in self.highlighted_spans:
            existing_tag_info = self.highlighted_spans[entity_span]
            if existing_tag_info["tag"] == tag_type and existing_tag_info["text"] == word:
                return
            else:
                QMessageBox.warning(self, "Annotation Exists", f"'{word}' at this position is already tagged as '{existing_tag_info['tag']}'.\nClear it first to change its type or content.")
                self.pop_state_from_undo_stack()
                return
        class_context_name = None
        if tag_type == "CLASS":
            if any(c.get("span") == entity_span for c in self.annotations["classes"]): return
            self.annotations["classes"].append({
                "name": word, "span": entity_span, "attributes": [], "methods": []
            })
            self.highlight_word(word, start, end, "CLASS")
        else:
            if not self.selected_context_class_span:
                QMessageBox.warning(self, "Error", "No context class selected for attribute/method.")
                self.pop_state_from_undo_stack()
                return
            parent_class_obj = self._get_class_by_span(self.selected_context_class_span)
            if not parent_class_obj:
                QMessageBox.critical(self, "Error", "Context class not found in annotations.")
                self.pop_state_from_undo_stack()
                return
            class_context_name = parent_class_obj["name"]
            if tag_type == "ATTRIBUTE":
                if any(attr.get("span") == entity_span for attr in parent_class_obj["attributes"]): return
                parent_class_obj["attributes"].append({
                    "name": word, "span": entity_span, "type": "String"
                })
                self.highlight_word(word, start, end, "ATTRIBUTE", class_context_name)
            elif tag_type == "METHOD":
                if any(m.get("span") == entity_span for m in parent_class_obj.get("methods",[])): return
                parent_class_obj.setdefault("methods", []).append({
                    "name": word, "span": entity_span, "parameters": []
                })
                self.highlight_word(word, start, end, "METHOD", class_context_name)
        if self.selected_context_class_span and self.current_tag in ["ATTRIBUTE", "METHOD"]:
            context_class_info = self._get_class_by_span(self.selected_context_class_span)
            if context_class_info:
                 self.status_label.setText(f"Added {tag_type} '{word}' to {context_class_info['name']}. Add more or select new.")

    def add_relationship(self, from_class_name, to_class_name, rel_type, from_span, to_span):
        if any(r["from_span"] == from_span and r["to_span"] == to_span and r["type"] == rel_type
               for r in self.annotations["relationships"]):
            return
        self.annotations["relationships"].append({
            "from_class": from_class_name, "to_class": to_class_name, "type": rel_type,
            "from_span": from_span, "to_span": to_span
        })

    def update_results(self, from_json_edit=False): # Added default value
        if not from_json_edit:
            try:
                serializable_annotations = copy.deepcopy(self.annotations)
                for cls in serializable_annotations["classes"]:
                    if "span" in cls: cls["span"] = list(cls["span"])
                    for attr in cls["attributes"]:
                        if "span" in attr: attr["span"] = list(attr["span"])
                    for meth in cls.get("methods", []):
                        if "span" in meth: meth["span"] = list(meth["span"])
                for rel in serializable_annotations["relationships"]:
                    if "from_span" in rel: rel["from_span"] = list(rel["from_span"])
                    if "to_span" in rel: rel["to_span"] = list(rel["to_span"])
                json_str = json.dumps(serializable_annotations, indent=2)
                self.results_editor.blockSignals(True)
                self.results_editor.setText(json_str)
                self.results_editor.blockSignals(False)
            except Exception as e:
                print(f"Error updating results: {e}")

    def handle_json_text_change(self):
        if self._is_processing_json_change: return
        self._is_processing_json_change = True
        try:
            json_text = self.results_editor.toPlainText()
            if not json_text.strip():
                self._is_processing_json_change = False
                return
            new_annotations_data = json.loads(json_text)
            if not isinstance(new_annotations_data, dict) or \
               "classes" not in new_annotations_data or \
               "relationships" not in new_annotations_data:
                self.status_label.setText("Error: Invalid JSON structure in results panel.")
                self._is_processing_json_change = False
                return
            self.push_state_to_undo_stack()
            for cls in new_annotations_data.get("classes", []):
                if "span" in cls and isinstance(cls["span"], list): cls["span"] = tuple(cls["span"])
                for attr in cls.get("attributes", []):
                    if "span" in attr and isinstance(attr["span"], list): attr["span"] = tuple(attr["span"])
                for meth in cls.get("methods", []):
                    if "span" in meth and isinstance(meth["span"], list): meth["span"] = tuple(meth["span"])
            for rel in new_annotations_data.get("relationships", []):
                 if "from_span" in rel and isinstance(rel["from_span"], list): rel["from_span"] = tuple(rel["from_span"])
                 if "to_span" in rel and isinstance(rel["to_span"], list): rel["to_span"] = tuple(rel["to_span"])
            self.annotations = new_annotations_data
            self.rebuild_highlights_from_annotations()
            self.status_label.setText("Annotations updated from JSON panel.")
            self.update_undo_redo_actions()
        except json.JSONDecodeError:
            self.status_label.setText("Warning: Invalid JSON in results. Not synced.")
        except Exception as e:
            self.status_label.setText(f"Error processing JSON update: {e}")
        finally:
            self._is_processing_json_change = False

    def rebuild_highlights_from_annotations(self):
        cursor = self.text_editor.textCursor()
        cursor.select(QTextCursor.Document)
        default_format = QTextCharFormat()
        cursor.setCharFormat(default_format)
        # Deselect everything after clearing format
        new_cursor = self.text_editor.textCursor()
        new_cursor.clearSelection()
        self.text_editor.setTextCursor(new_cursor)
        self.highlighted_spans.clear()

        for cls in self.annotations.get("classes", []):
            span = cls.get("span")
            if span and len(span) == 2:
                self.highlight_word(cls["name"], span[0], span[1], "CLASS")
            for attr in cls.get("attributes", []):
                attr_span = attr.get("span")
                if attr_span and len(attr_span) == 2:
                    self.highlight_word(attr["name"], attr_span[0], attr_span[1], "ATTRIBUTE", cls["name"])
            for meth in cls.get("methods", []):
                meth_span = meth.get("span")
                if meth_span and len(meth_span) == 2:
                    self.highlight_word(meth["name"], meth_span[0], meth_span[1], "METHOD", cls["name"])
        if self.selected_context_class_span: # Reapply context highlight if still valid
            context_class_info = self._get_class_by_span(self.selected_context_class_span) # Get fresh info
            if context_class_info and \
               self.selected_context_class_span in self.highlighted_spans and \
               self.highlighted_spans[self.selected_context_class_span]["tag"] == "CLASS":
                 self._apply_highlight(self.selected_context_class_span[0], self.selected_context_class_span[1], "CONTEXT_CLASS", temporary=True)
            else: # Context no longer valid (e.g. class was deleted/modified via JSON)
                self.selected_context_class_span = None


    def export_json(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(self, "Save JSON File", "", "JSON Files (*.json);;All Files (*)", options=options)
        if file_name:
            try:
                text_content = self.text_editor.toPlainText()
                export_data_annotations = copy.deepcopy(self.annotations)
                for cls in export_data_annotations["classes"]:
                    if "span" in cls: cls["span"] = list(cls["span"])
                    for attr in cls["attributes"]:
                        if "span" in attr: attr["span"] = list(attr["span"])
                    for meth in cls.get("methods", []):
                        if "span" in meth: meth["span"] = list(meth["span"])
                for rel in export_data_annotations["relationships"]:
                    if "from_span" in rel: rel["from_span"] = list(rel["from_span"])
                    if "to_span" in rel: rel["to_span"] = list(rel["to_span"])
                export_data = {"text": text_content, "annotations": export_data_annotations}
                with open(file_name, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2)
                QMessageBox.information(self, "Success", "Annotations and text exported.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export JSON: {str(e)}")

    def load_text(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Text File", "", "Text Files (*.txt);;All Files (*)", options=options)
        if file_name:
            try:
                self.text_editor.setReadOnly(False)
                self.clear_all_internal(for_load=True)
                with open(file_name, 'r', encoding='utf-8', errors='replace') as file:
                    content = file.read()
                    self.text_editor.setPlainText(content)
                self.status_label.setText(f"File loaded: {file_name}. Annotate or edit.")
                self.text_editor.setReadOnly(False)
                self.push_state_to_undo_stack(initial_state=True)
                self.update_results()
                self.update_undo_redo_actions()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load file: {str(e)}")
                self.clear_all_internal(for_load=True)
                self.push_state_to_undo_stack(initial_state=True)

    def clear_annotations_action(self):
        self.clear_annotations(push_undo=True)

    def clear_annotations(self, push_undo=False):
        if push_undo: self.push_state_to_undo_stack()
        self.annotations = {"classes": [], "relationships": []}
        cursor = self.text_editor.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        cursor.setCharFormat(fmt)
        cursor.clearSelection()
        self.text_editor.setTextCursor(cursor)
        self.highlighted_spans.clear()
        self._reset_tag_buttons()
        self.current_tag = None
        self.relationship_mode = False
        self.current_relationship = ""
        self.relationship_from_class_span = None
        self.selected_context_class_span = None
        self.update_results()
        self.status_label.setText("Annotations cleared. Text preserved. Select tag to annotate.")
        if push_undo: self.update_undo_redo_actions()

    def clear_all_internal(self, for_load=False):
        self.text_editor.setReadOnly(False)
        if not for_load: self.text_editor.clear()
        self.annotations = {"classes": [], "relationships": []}
        self.highlighted_spans.clear()
        self._reset_tag_buttons()
        self.current_tag = None
        self.relationship_mode = False
        self.current_relationship = ""
        self.relationship_from_class_span = None
        self.selected_context_class_span = None
        if for_load:
            cursor = self.text_editor.textCursor()
            cursor.select(QTextCursor.Document)
            fmt = QTextCharFormat()
            cursor.setCharFormat(fmt)
            cursor.clearSelection()
            self.text_editor.setTextCursor(cursor)
        self.update_results()
        if not for_load:
            self.status_label.setText("All content cleared. Load file or enter text.")
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.push_state_to_undo_stack(initial_state=True)
            self.update_undo_redo_actions()

    def clear_all(self):
        self.clear_all_internal(for_load=False)

    def push_state_to_undo_stack(self, initial_state=False):
        if not initial_state:
            if self.undo_stack and self.undo_stack[-1]["annotations"] == self.annotations and \
               self.undo_stack[-1]["highlighted_spans"] == self.highlighted_spans and \
               self.undo_stack[-1]["selected_context_class_span"] == self.selected_context_class_span:
                return
        current_state = {
            "annotations": copy.deepcopy(self.annotations),
            "highlighted_spans": copy.deepcopy(self.highlighted_spans),
            "selected_context_class_span": self.selected_context_class_span,
        }
        self.undo_stack.append(current_state)
        if not initial_state: # Don't clear redo stack if it's the initial state push
            self.redo_stack.clear()
        self.update_undo_redo_actions()

    def pop_state_from_undo_stack(self):
        if self.undo_stack:
            if len(self.undo_stack) > 1 or (len(self.undo_stack) == 1 and not self.redo_stack): # Avoid popping initial state if it's the only one
                self.undo_stack.pop()
                self.update_undo_redo_actions()


    def _restore_state(self, state):
        self.annotations = copy.deepcopy(state["annotations"])
        self.highlighted_spans = copy.deepcopy(state["highlighted_spans"])
        self.selected_context_class_span = state.get("selected_context_class_span")
        self.rebuild_highlights_from_annotations()
        self.update_results(from_json_edit=True)
        self._reset_tag_buttons()
        self.current_tag = None
        self.relationship_mode = False
        self.relationship_from_class_span = None

    def undo_annotation(self):
        if len(self.undo_stack) > 1:
            current_state_for_redo = self.undo_stack.pop() # Current state becomes the one to redo
            self.redo_stack.append(current_state_for_redo)
            state_to_restore = self.undo_stack[-1]
            self._restore_state(state_to_restore)
            self.status_label.setText("Undo successful.")
        else:
            self.status_label.setText("Nothing more to undo.")
        self.update_undo_redo_actions()

    def redo_annotation(self):
        if self.redo_stack:
            state_to_restore = self.redo_stack.pop()
            self.undo_stack.append(state_to_restore) # The redone state is now on undo stack
            self._restore_state(state_to_restore)
            self.status_label.setText("Redo successful.")
        else:
            self.status_label.setText("Nothing to redo.")
        self.update_undo_redo_actions()

    def update_undo_redo_actions(self):
        self.undo_action.setEnabled(len(self.undo_stack) > 1)
        self.redo_action.setEnabled(bool(self.redo_stack))

    # --- Pre-annotation Methods ---
    def _initialize_nlp_components(self):
        global NLP_PREANNOTATE, MATCHER_PREANNOTATE # Use global to ensure they are shared
        if not SPACY_AVAILABLE:
            return False
        
        # Check if the instance variable for the model is already loaded
        if self.nlp_model_preannotate is None:
            try:
                original_status = self.status_label.text()
                self.status_label.setText("Loading NLP model for pre-annotation (this may take a moment)...")
                QApplication.processEvents()
                
                # You can change "en_core_web_sm" to "en_core_web_md" or "en_core_web_lg"
                # Ensure the chosen model is downloaded: python -m spacy download en_core_web_lg
                model_name = "en_core_web_trf" # Change as needed
                NLP_PREANNOTATE = spacy.load(model_name)
                self.nlp_model_preannotate = NLP_PREANNOTATE # Store on instance
                
                MATCHER_PREANNOTATE = Matcher(NLP_PREANNOTATE.vocab)
                # self.matcher_preannotate = MATCHER_PREANNOTATE # If you want an instance copy

                self.status_label.setText(f"NLP model ({model_name}) loaded.")
                QApplication.processEvents() # Show "model loaded" message
                # Potentially revert to original_status after a short delay or keep it
                return True
            except Exception as e:
                QMessageBox.critical(self, "NLP Model Error", f"Failed to load spaCy model: {e}\nEnsure your chosen model (e.g., en_core_web_sm) is downloaded. Pre-annotation disabled.")
                self.preannotate_btn.setEnabled(False)
                self.nlp_model_preannotate = None # Ensure it's reset on failure
                NLP_PREANNOTATE = None
                return False
        return True # Already initialized

    def _syntactic_rules_for_entities(self, doc):
        results = []
        # More liberal class identification initially: Proper nouns, specific common nouns
        for token in doc:
            # CLASS: Nouns that are subjects, objects, or proper nouns often starting with uppercase
            if token.pos_ in ("NOUN", "PROPN") and token.dep_ in ("nsubj", "nsubjpass", "pobj", "dobj", "conj", "ROOT", "appos"):
                if len(token.text) > 2 and (token.text[0].isupper() or token.pos_ == "PROPN"):
                    results.append({
                        "text": token.text, "label": "CLASS",
                        "start": token.idx, "end": token.idx + len(token.text)
                    })
            # ATTRIBUTE: Nouns that are objects, attributes, appositions, or compound parts
            elif token.pos_ == "NOUN" and token.dep_ in ("dobj", "attr", "appos", "conj", "compound", "pobj"):
                if len(token.text) > 1:
                    results.append({
                        "text": token.text, "label": "ATTRIBUTE",
                        "start": token.idx, "end": token.idx + len(token.text)
                    })
            # METHOD: Verbs, especially if they are roots or conjuncts in a verb phrase
            elif token.pos_ == "VERB" and token.dep_ in ("ROOT", "conj", "xcomp", "ccomp", "acl"):
                # Heuristic: often followed by a noun (direct object) or part of a verb phrase
                # This could be refined by checking for subsequent parentheses in code-like text.
                if len(token.text) > 2: # and any(child.pos_ == "NOUN" for child in token.children):
                     results.append({
                        "text": token.text, "label": "METHOD",
                        "start": token.idx, "end": token.idx + len(token.text)
                    })
        return sorted(results, key=lambda x: x["start"])


    def run_preannotation_pipeline(self):
        if not self._initialize_nlp_components(): # Ensures self.nlp_model_preannotate is set
            return

        text_content = self.text_editor.toPlainText()
        if not text_content.strip():
            self.status_label.setText("No text to pre-annotate.")
            return

        progress = QProgressDialog("Pre-annotating entities...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0) # Show immediately
        progress.setValue(0)
        QApplication.processEvents()

        try:
            self.status_label.setText("Processing text with NLP model...")
            progress.setLabelText("Processing text with NLP model...")
            progress.setValue(10) # Arbitrary small value
            QApplication.processEvents()

            if self.nlp_model_preannotate is None: # Should have been caught by _initialize_nlp_components
                QMessageBox.critical(self, "Error", "NLP model not available for pre-annotation.")
                progress.close()
                return

            doc = self.nlp_model_preannotate(text_content)
            progress.setValue(30) # After main NLP processing
            progress.setLabelText("Applying syntactic rules...")
            QApplication.processEvents()

            identified_entities = self._syntactic_rules_for_entities(doc)
            progress.setValue(50)
            QApplication.processEvents()

            if progress.wasCanceled():
                self.status_label.setText("Pre-annotation cancelled.")
                progress.close()
                return

            if not identified_entities:
                self.status_label.setText("No potential entities found by pre-annotation rules.")
                progress.close()
                return

            self.push_state_to_undo_stack()
            self.annotations = {"classes": [], "relationships": []} # Start fresh for pre-annotation
            self.highlighted_spans.clear()

            current_class_obj = None
            newly_added_spans = set()
            
            progress.setLabelText("Structuring identified entities...")
            progress.setMaximum(50 + len(identified_entities)) # Adjust max for the loop

            for i, entity in enumerate(identified_entities):
                progress.setValue(50 + i)
                if progress.wasCanceled():
                    self.status_label.setText("Pre-annotation cancelled during entity structuring.")
                    # Potentially revert to state before push_state_to_undo_stack or handle partially processed
                    self.pop_state_from_undo_stack() # Revert the push since it was cancelled
                    self.rebuild_highlights_from_annotations() # Rebuild from potentially empty/old state
                    self.update_results()
                    progress.close()
                    return

                entity_name = entity["text"]
                entity_label = entity["label"]
                entity_start = entity["start"]
                entity_end = entity["end"]
                entity_span = (entity_start, entity_end)

                if entity_span in newly_added_spans: continue
                is_overlapping = any(max(entity_start, es[0]) < min(entity_end, es[1]) for es in newly_added_spans)
                if is_overlapping: continue

                if entity_label == "CLASS":
                    if not any(c["name"] == entity_name and c["span"] == entity_span for c in self.annotations["classes"] ): # More precise duplicate check
                        class_data = {"name": entity_name, "span": entity_span, "attributes": [], "methods": []}
                        self.annotations["classes"].append(class_data)
                        current_class_obj = class_data
                        # self.highlight_word(entity_name, entity_start, entity_end, "CLASS") # Highlights will be rebuilt later
                        newly_added_spans.add(entity_span)
                elif entity_label == "ATTRIBUTE" and current_class_obj:
                    if not any(a["name"] == entity_name and a["span"] == entity_span for a in current_class_obj["attributes"]):
                        current_class_obj["attributes"].append({"name": entity_name, "span": entity_span, "type": "String"})
                        # self.highlight_word(entity_name, entity_start, entity_end, "ATTRIBUTE", current_class_obj["name"])
                        newly_added_spans.add(entity_span)
                elif entity_label == "METHOD" and current_class_obj:
                    if not any(m["name"] == entity_name and m["span"] == entity_span for m in current_class_obj.get("methods", [])):
                        current_class_obj.setdefault("methods", []).append({"name": entity_name, "span": entity_span, "parameters": []})
                        # self.highlight_word(entity_name, entity_start, entity_end, "METHOD", current_class_obj["name"])
                        newly_added_spans.add(entity_span)
            
            progress.setValue(50 + len(identified_entities))
            progress.setLabelText("Finalizing annotations...")
            QApplication.processEvents()

            self.rebuild_highlights_from_annotations() # Rebuild all highlights once at the end
            self.update_results()
            self.update_undo_redo_actions()
            self.status_label.setText(f"Pre-annotation applied. Review the results.")
            progress.close()

        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Pre-annotation Error", f"An error occurred during pre-annotation: {str(e)}")
            self.status_label.setText("Pre-annotation failed.")
            # Potentially revert to the previous state if an error occurs mid-way
            if len(self.undo_stack) > 1 and self.undo_stack[-1]['annotations'] != self.annotations : # if state was pushed and annotations changed
                 self.undo_annotation() # Try to revert to state before pre-annotation started
                 self.redo_stack.pop() # Clear the failed pre-annotation from redo
                 self.status_label.setText("Pre-annotation failed. State reverted.")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = AnnotationTool()
    sys.exit(app.exec_())
