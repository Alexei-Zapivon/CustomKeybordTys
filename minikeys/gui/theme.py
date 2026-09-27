"""Цвета, стиль и иконка приложения (рисуется кодом — файлы картинок не нужны)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

BG = "#1b1d23"
PANEL = "#23262e"
KEY = "#2c3039"
KEY_HOVER = "#353a45"
BORDER = "#434956"
ACCENT = "#5b8cff"
FLASH = "#ffb547"
TEXT = "#e8eaee"
SUBTLE = "#8d94a3"
OK = "#3ecf8e"
WARN = "#f5a524"
ERROR = "#f25f5c"
GRID = "#262932"

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 10pt; }}
QToolBar {{ background: {PANEL}; border: none; padding: 6px; spacing: 6px; }}
QToolButton {{ background: {KEY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}
QToolButton:hover {{ background: {KEY_HOVER}; }}
QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton {{ background: {KEY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: {KEY_HOVER}; }}
QPushButton:default {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit[recording="true"] {{ border: 2px solid {FLASH}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {ACCENT}; }}
QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QListWidget::item {{ padding: 8px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: {PANEL}; padding: 7px 14px; border: 1px solid {BORDER};
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {KEY_HOVER}; }}
QStatusBar {{ background: {PANEL}; color: {SUBTLE}; }}
QLabel#hint {{ color: {SUBTLE}; }}
QLabel#panelTitle {{ color: {SUBTLE}; font-weight: bold; padding: 2px 2px 6px 2px; }}
QWidget#profilePanel QListWidget::item {{ padding: 9px 6px; }}
QLabel#banner {{ background: {FLASH}; color: #1b1d23; padding: 10px; font-weight: bold; border-radius: 6px; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {SUBTLE}; border-radius: 4px;
    background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""


def app_icon(active: bool = True) -> QIcon:
    """Мини-клавиатура 2×2: цветная — перехват включён, серая — выключен."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT if active else SUBTLE))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.setBrush(QColor("white"))
        pad, gap = size * 0.2, size * 0.08
        cell = (size - 2 * pad - gap) / 2
        for i in range(2):
            for j in range(2):
                p.drawRoundedRect(QRectF(pad + i * (cell + gap), pad + j * (cell + gap), cell, cell),
                                  size * 0.05, size * 0.05)
        p.end()
        icon.addPixmap(pm)
    return icon
