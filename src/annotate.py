import sys
import json
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QTextEdit, QLabel, QFileDialog, QMessageBox,
                             QProgressDialog)
from PyQt5.QtGui import QTextCharFormat, QColor, QFont, QTextCursor, QTextOption
from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QSize

class TextSelector(QTextEdit):
    """Custom QTextEdit that handles word selection and highlighting"""
    wordSelected = pyqtSignal(str, int, int)  # word, start position, end position

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self.currentWordStart = -1
        self.currentWordEnd = -1
        self.setReadOnly(False)  # Allow editing initially

        # Optimize for large documents
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

        # Set a reasonable document size limit (high enough for large files)
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
        super().mousePressEvent(event)

class AnnotationTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.annotations = {
            "classes": [],
            "relationships": []
        }
        # Dict to keep track of highlighted positions
        self.highlighted_spans = {}
        # Keep track of relationship selection state
        self.relationship_mode = False
        self.current_relationship = ""
        self.relationship_from_class = None
        self.relationship_to_class = None
        # Define tag colors
        self.tag_colors = {
            "CLASS": QColor(255, 200, 200),      # Light red
            "ATTRIBUTE": QColor(200, 255, 200),  # Light green
            "METHOD": QColor(200, 200, 255),     # Light blue
            "association": QColor(255, 255, 200),  # Light yellow
            "generalization": QColor(255, 200, 255),  # Light purple
            "composition": QColor(200, 255, 255)   # Light cyan
        }

    def initUI(self):
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # Text annotation area
        text_header_layout = QHBoxLayout()
        self.text_label = QLabel("Text to annotate:")
        text_header_layout.addWidget(self.text_label)

        # Add text manipulation buttons to the header
        self.load_text_btn = QPushButton("Load Text File")
        self.load_text_btn.clicked.connect(self.load_text)
        text_header_layout.addWidget(self.load_text_btn)

        self.clear_text_btn = QPushButton("Clear Text")
        self.clear_text_btn.clicked.connect(self.clear_text)
        text_header_layout.addWidget(self.clear_text_btn)

        main_layout.addLayout(text_header_layout)

        self.text_editor = TextSelector()
        self.text_editor.setMinimumHeight(200)
        self.text_editor.wordSelected.connect(self.handle_word_selection)
        # Set document size limit to a large value to handle big files
        self.text_editor.document().setMaximumBlockCount(1000000)
        main_layout.addWidget(self.text_editor)

        # Button area for entity tags
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

        # Button area for relationship tags
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

        # Status label
        self.status_label = QLabel("Select a tag button, then click on a word to annotate.")
        main_layout.addWidget(self.status_label)

        # Results and Export
        results_header_layout = QHBoxLayout()
        results_label = QLabel("Annotation Results (Editable JSON):") # Updated Label
        results_header_layout.addWidget(results_label)

        self.export_btn = QPushButton("Export JSON")
        self.export_btn.clicked.connect(self.export_json)
        results_header_layout.addWidget(self.export_btn)

        self.clear_annotations_btn = QPushButton("Clear Annotations")
        self.clear_annotations_btn.clicked.connect(self.clear_annotations)
        results_header_layout.addWidget(self.clear_annotations_btn)

        main_layout.addLayout(results_header_layout)

        self.results_editor = QTextEdit()
        self.results_editor.setMinimumHeight(200)
        self.results_editor.setReadOnly(False)
        main_layout.addWidget(self.results_editor)

        # Set window properties
        self.setGeometry(100, 100, 900, 800)
        self.setWindowTitle('Code Document Annotation Tool')
        self.show()

        # Initial text
        sample_text = "Each account is associated with a user profile containing name, age, and address."
        self.text_editor.setText(sample_text)

        # Current tag mode
        self.current_tag = None

    def set_tag_mode(self, tag):
        """Set the current tagging mode"""
        # Reset all tag buttons
        self.class_btn.setChecked(False)
        self.attribute_btn.setChecked(False)
        self.method_btn.setChecked(False)
        self.association_btn.setChecked(False)
        self.generalization_btn.setChecked(False)
        self.composition_btn.setChecked(False)

        # Set the selected tag
        if tag == "CLASS":
            self.class_btn.setChecked(True)
        elif tag == "ATTRIBUTE":
            self.attribute_btn.setChecked(True)
        elif tag == "METHOD":
            self.method_btn.setChecked(True)

        self.current_tag = tag
        self.relationship_mode = False
        self.relationship_from_class = None
        self.relationship_to_class = None
        self.status_label.setText(f"Selected tag: {tag}. Click on a word to annotate.")

    def set_relationship_mode(self, relationship_type):
        """Set the current relationship tagging mode"""
        # Reset all tag buttons
        self.class_btn.setChecked(False)
        self.attribute_btn.setChecked(False)
        self.method_btn.setChecked(False)
        self.association_btn.setChecked(False)
        self.generalization_btn.setChecked(False)
        self.composition_btn.setChecked(False)
        if relationship_type == "association":
            self.association_btn.setChecked(True)
        elif relationship_type == "generalization":
            self.generalization_btn.setChecked(True)
        elif relationship_type == "composition":
            self.composition_btn.setChecked(True)

        self.relationship_mode = True
        self.current_relationship = relationship_type
        self.current_tag = None
        self.relationship_from_class = None
        self.relationship_to_class = None
        self.status_label.setText(f"Selected relationship: {relationship_type}. Click on first class (FROM), then second class (TO).")

    def export_json(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(self, "Save JSON File", "", "JSON Files (*.json);;All Files (*)", options=options)

        if file_name:
            try:
                text_content = self.text_editor.toPlainText().replace('\n', ' ')
                annotations_json_str = self.results_editor.toPlainText()
                try:
                    annotations_data = json.loads(annotations_json_str)
                except json.JSONDecodeError as e:
                    QMessageBox.critical(self, "Invalid JSON in Results Panel",
                                         f"The JSON in the results panel is malformed and cannot be parsed.\n"
                                         f"Please correct it before exporting.\n\nError: {str(e)}")
                    return # Stop export if JSON is invalid

                # Prepare the data to be exported
                export_data = {
                    "text": text_content,
                    "annotations": annotations_data 
                }

                with open(file_name, 'w', encoding='utf-8') as file:
                    json.dump(export_data, file, indent=2)
                QMessageBox.information(self, "Success", "Annotations and text exported successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export JSON: {str(e)}")


    def handle_word_selection(self, word, start, end):
        """Handle word selection in the text editor"""
        # Make text editor read-only during annotation to avoid accidental text modifications
        self.text_editor.setReadOnly(True)

        if self.current_tag and not self.relationship_mode:
            # Entity tagging mode
            self.highlight_word(word, start, end, self.current_tag)
            self.add_entity(word, self.current_tag)
            self.update_results()
        elif self.relationship_mode:
            # Relationship tagging mode
            cursor = self.text_editor.textCursor()
            cursor.setPosition(start)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, end - start)

            # Check if selected word is a class
            is_class = False
            class_info = None

            for class_obj in self.annotations["classes"]:
                if class_obj["name"].lower() == word.lower(): # Case-insensitive check for class name
                    is_class = True
                    class_info = class_obj
                    break
            
            if not is_class:
                self.status_label.setText(f"'{word}' is not a tagged CLASS. Please select a class entity.")
                return

            if not self.relationship_from_class:
                # First class in relationship
                self.relationship_from_class = class_info["name"]
                self.status_label.setText(f"From: {self.relationship_from_class}. Now select the second class (TO).")
            else:
                # Second class in relationship
                if class_info["name"].lower() == self.relationship_from_class.lower():
                    self.status_label.setText(f"Cannot relate a class to itself in this manner. From: {self.relationship_from_class}. Select a different second class (TO).")
                    return

                self.relationship_to_class = class_info["name"]
                self.add_relationship(self.relationship_from_class, self.relationship_to_class, self.current_relationship)
                self.status_label.setText(f"Added relationship: {self.relationship_from_class} --[{self.current_relationship}]--> {self.relationship_to_class}")
                # Reset relationship selection
                self.relationship_from_class = None
                self.relationship_to_class = None
                self.update_results()

    def highlight_word(self, word, start, end, tag_type):
        """Highlight the selected word with the appropriate color"""
        cursor = self.text_editor.textCursor()
        cursor.setPosition(start)
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, end - start)

        # Format with color based on tag type
        format = QTextCharFormat()
        format.setBackground(self.tag_colors[tag_type])
        cursor.mergeCharFormat(format)

        # Store highlighted position
        self.highlighted_spans[(start, end)] = {"text": word, "tag": tag_type}

    def add_entity(self, word, tag_type):
        """Add entity to the annotations"""
        if tag_type == "CLASS":
            # Check if class already exists
            for class_obj in self.annotations["classes"]:
                if class_obj["name"].lower() == word.lower():
                    return

            # Add new class
            self.annotations["classes"].append({
                "name": word,
                "attributes": [],
                "methods": []
            })
        elif tag_type == "ATTRIBUTE":
            # Find the most recently tagged class
            if not self.annotations["classes"]:
                QMessageBox.warning(self, "Warning", "Please tag a CLASS before tagging attributes.")
                return
            # For simplicity, add attribute to the last class
            last_class = self.annotations["classes"][-1]

            # Check if attribute already exists
            for attr in last_class["attributes"]:
                if attr["name"].lower() == word.lower():
                    return
            # Add attribute with default string type
            last_class["attributes"].append({
                "name": word,
                "type": "String"  # Default type
            })
        elif tag_type == "METHOD":
            # Find the most recently tagged class
            if not self.annotations["classes"]:
                QMessageBox.warning(self, "Warning", "Please tag a CLASS before tagging methods.")
                return

            last_class = self.annotations["classes"][-1]

            if "methods" not in last_class: # Ensure 'methods' list exists
                last_class["methods"] = []

            # Check if method already exists (case-insensitive)
            for method_entry in last_class.get("methods", []):
                if isinstance(method_entry, dict) and method_entry.get("name", "").lower() == word.lower():
                    return
                elif isinstance(method_entry, str) and method_entry.lower() == word.lower(): # Legacy or simple format
                    return
            
            # Add method
            last_class["methods"].append({
                "name": word,
                "parameters": []  # Default empty parameters
            })

    def add_relationship(self, from_class, to_class, rel_type):
        """Add relationship to the annotations"""
        # Check if relationship already exists
        for rel in self.annotations["relationships"]:
            if rel["from"].lower() == from_class.lower() and \
               rel["to"].lower() == to_class.lower() and \
               rel["type"] == rel_type:
                return

        # Add new relationship
        self.annotations["relationships"].append({
            "from": from_class,
            "to": to_class,
            "type": rel_type
        })

    def update_results(self):
        json_str = json.dumps(self.annotations, indent=2)
        self.results_editor.setText(json_str)

    def load_text(self):
        """Load text from a file with efficient handling for large files"""
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Text File", "", "Text Files (*.txt);;All Files (*)", options=options)
        if file_name:
            try:
                self.text_editor.setReadOnly(False)
                self.text_editor.clear()
                chunk_size = 1024 * 1024
                with open(file_name, 'r', encoding='utf-8', errors='replace') as file:
                    text_chunk = file.read(chunk_size)
                    if text_chunk:
                        self.text_editor.setPlainText(text_chunk)
                    while True:
                        text_chunk = file.read(chunk_size)
                        if not text_chunk:
                            break
                        cursor = self.text_editor.textCursor()
                        cursor.movePosition(QTextCursor.End)
                        cursor.insertText(text_chunk)
                
                self.clear_annotations()
                self.status_label.setText(f"File loaded: {file_name}. You can now edit or annotate.")
                self.text_editor.setReadOnly(False) # Ensure it's editable

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load file: {str(e)}")


    def clear_text(self):
        """Clear the text editor and all annotations"""
        self.text_editor.setReadOnly(False) # Make editable
        self.text_editor.clear()
        self.clear_annotations() # This will also clear results_editor
        self.status_label.setText("Text cleared. You can type, paste new text, or load a file.")


    def clear_annotations(self):
        """Clear all annotations but keep the text"""
        current_text = self.text_editor.toPlainText() # Preserve text
        self.annotations = {
            "classes": [],
            "relationships": []
        }
        self.highlighted_spans = {}
        self.text_editor.setReadOnly(False)
        self.text_editor.clear() 
        self.text_editor.setPlainText(current_text)
        self.class_btn.setChecked(False)
        self.attribute_btn.setChecked(False)
        self.method_btn.setChecked(False)
        self.association_btn.setChecked(False)
        self.generalization_btn.setChecked(False)
        self.composition_btn.setChecked(False)
        self.current_tag = None
        self.relationship_mode = False
        self.current_relationship = ""
        self.relationship_from_class = None
        self.relationship_to_class = None

        self.update_results()
        self.status_label.setText("Annotations cleared. Select a tag button, then click on a word to annotate.")

    def clear_all(self):
        """Clear text and annotations"""
        self.clear_text()
        self.status_label.setText("All content cleared. You can load a new file or enter text.")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = AnnotationTool()
    sys.exit(app.exec_())