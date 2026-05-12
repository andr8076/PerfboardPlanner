from __future__ import annotations

APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #f6f7fb;
    color: #172033;
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 10pt;
}
QToolBar {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #dfe4ee;
    spacing: 6px;
    padding: 8px;
}
QToolButton, QPushButton {
    background: #ffffff;
    border: 1px solid #d7deeb;
    border-radius: 8px;
    padding: 7px 10px;
}
QToolButton:hover, QPushButton:hover {
    background: #eef4ff;
    border-color: #9dbcf5;
}
QToolButton:checked, QPushButton:checked {
    background: #2457d6;
    color: #ffffff;
    border-color: #2457d6;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #ffffff;
    border-bottom: 1px solid #dfe4ee;
    padding: 8px;
    font-weight: 600;
}
QTabWidget::pane {
    border: 1px solid #dfe4ee;
    border-radius: 10px;
    background: #ffffff;
}
QTabBar::tab {
    background: #edf1f7;
    border: 1px solid #dfe4ee;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 10px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #2457d6;
    font-weight: 600;
}
QLineEdit, QTextEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #d7deeb;
    border-radius: 8px;
    padding: 6px;
}
QListWidget, QTreeWidget, QTableWidget {
    background: #ffffff;
    border: 1px solid #dfe4ee;
    border-radius: 8px;
    alternate-background-color: #f4f6fb;
}
QHeaderView::section {
    background: #f1f4f9;
    border: 0;
    border-bottom: 1px solid #dfe4ee;
    padding: 6px;
    font-weight: 600;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #dfe4ee;
    border-radius: 12px;
    margin-top: 12px;
    padding: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    font-weight: 700;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #dfe4ee;
}
"""
