"""Боковая панель «Профили»: список, создание, копия, переименование, удаление."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QVBoxLayout, QWidget)


class ProfilePanel(QWidget):
    activated = Signal(str)            # пользователь выбрал профиль
    create_requested = Signal()
    duplicate_requested = Signal(str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("profilePanel")
        self.setFixedWidth(210)
        self._updating = False

        title = QLabel("Профили")
        title.setObjectName("panelTitle")
        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.currentItemChanged.connect(self._current_changed)
        self.list.itemDoubleClicked.connect(lambda item: self.rename_requested.emit(item.data(Qt.UserRole)))

        new = QPushButton("＋ Новый профиль")
        new.setObjectName("primary")
        new.clicked.connect(self.create_requested)
        dup = QPushButton("Сделать копию")
        dup.clicked.connect(lambda: self._emit_current(self.duplicate_requested))
        ren = QPushButton("Переименовать")
        ren.clicked.connect(lambda: self._emit_current(self.rename_requested))
        self.delete = QPushButton("Удалить")
        self.delete.clicked.connect(lambda: self._emit_current(self.delete_requested))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 0, 10)
        lay.addWidget(title)
        lay.addWidget(self.list, 1)
        for button in (new, dup, ren, self.delete):
            lay.addWidget(button)

    def set_profiles(self, names: list[str], active: str) -> None:
        self._updating = True
        try:
            self.list.clear()
            for name in names:
                item = QListWidgetItem(("●  " if name == active else "    ") + name)
                item.setData(Qt.UserRole, name)
                if name == active:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.list.addItem(item)
                if name == active:
                    self.list.setCurrentItem(item)
            self.delete.setEnabled(len(names) > 1)
        finally:
            self._updating = False

    def current_name(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _current_changed(self, item: QListWidgetItem | None, _previous) -> None:
        if not self._updating and item is not None:
            self.activated.emit(item.data(Qt.UserRole))

    def _emit_current(self, signal) -> None:
        name = self.current_name()
        if name:
            signal.emit(name)

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Переименовать…", lambda: self.rename_requested.emit(name))
        menu.addAction("Сделать копию", lambda: self.duplicate_requested.emit(name))
        remove = menu.addAction("Удалить", lambda: self.delete_requested.emit(name))
        remove.setEnabled(self.list.count() > 1)
        menu.exec(self.list.mapToGlobal(pos))
