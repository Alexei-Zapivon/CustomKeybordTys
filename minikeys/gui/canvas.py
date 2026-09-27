"""Поле с виртуальной мини-клавиатурой: кнопки и крутилки, перетаскиваемые мышью."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView,
                               QStyleOptionGraphicsItem, QWidget)

from .. import bindtext, store
from . import theme

SNAP = 8           # шаг примагничивания при перетаскивании
CLICK_SLOP = 4     # сдвиг меньше этого — это клик, а не перетаскивание


def _snap(p: QPointF) -> QPointF:
    return QPointF(round(p.x() / SNAP) * SNAP, round(p.y() / SNAP) * SNAP)


class _DraggableItem(QGraphicsObject):
    """Общее для кнопок и крутилок: перетаскивание с сеткой, клик, контекстное меню."""
    clicked = Signal(object)          # QPointF — точка клика в координатах элемента
    moved = Signal()
    context_requested = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__()
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)
        self._hover = False
        self._press_pos: QPointF | None = None

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return _snap(value)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = self.pos()
            self.setCursor(Qt.ClosedHandCursor)
            self.setZValue(10)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.OpenHandCursor)
        self.setZValue(0)
        if event.button() != Qt.LeftButton or self._press_pos is None:
            return
        delta = self.pos() - self._press_pos
        self._press_pos = None
        if abs(delta.x()) < CLICK_SLOP and abs(delta.y()) < CLICK_SLOP:
            self.setPos(self.pos() - delta)  # дрогнула рука — не сдвигаем
            self.clicked.emit(event.pos())
        else:
            self.moved.emit()

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(event.screenPos())


def _draw_text(p: QPainter, rect: QRectF, text: str, color: str, size: float,
               bold: bool = False, flags=Qt.AlignCenter | Qt.TextWordWrap,
               min_size: float | None = None) -> None:
    """Текст в прямоугольнике; с min_size шрифт уменьшается, пока текст не влезет."""
    font = QFont(p.font())
    font.setBold(bold)
    if min_size is not None:
        text = text.replace("+", "+\u200b")  # разрешаем перенос после «+»
    while True:
        font.setPointSizeF(size)
        if min_size is None or size <= min_size:
            break
        bounds = QFontMetricsF(font).boundingRect(rect, int(flags), text)
        if bounds.width() <= rect.width() + 0.5 and bounds.height() <= rect.height() + 0.5:
            break
        size -= 0.5
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(rect, flags, text)


class KeyItem(_DraggableItem):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.title = key
        self.caption = ""
        self._lit = False

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, store.KEY_SIZE, store.KEY_SIZE)

    def set_texts(self, title: str, caption: str) -> None:
        self.title, self.caption = title, caption
        self.update()

    def light(self, on: bool) -> None:
        self._lit = on
        self.update()

    def paint(self, p: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        r = self.boundingRect().adjusted(1, 1, -1, -1)
        p.setRenderHint(QPainter.Antialiasing)
        fill = theme.FLASH if self._lit else (theme.KEY_HOVER if self._hover else theme.KEY)
        p.setBrush(QColor(fill))
        p.setPen(QPen(QColor(theme.ACCENT if self.caption else theme.BORDER), 1.5))
        p.drawRoundedRect(r, 10, 10)
        # "клавишная" тень снизу
        p.setPen(QPen(QColor(0, 0, 0, 70), 3))
        p.drawLine(QPointF(r.left() + 8, r.bottom() - 1), QPointF(r.right() - 8, r.bottom() - 1))

        dark = self._lit
        _draw_text(p, r.adjusted(8, 5, -8, 0), self.title, "#1b1d23" if dark else theme.SUBTLE, 8.5,
                   flags=Qt.AlignLeft | Qt.AlignTop)
        caption = self.caption or "—"
        color = "#1b1d23" if dark else (theme.TEXT if self.caption else theme.SUBTLE)
        _draw_text(p, r.adjusted(5, 18, -5, -5), caption, color, 10, bold=bool(self.caption),
                   min_size=6.5)


class EncoderItem(_DraggableItem):
    """Крутилка: левая половина — поворот влево, правая — вправо, центр — нажатие."""
    PARTS = ("left", "right", "press")

    def __init__(self, enc_id: str) -> None:
        super().__init__()
        self.enc_id = enc_id
        self.name = ""
        self.captions = {part: "" for part in self.PARTS}
        self.keys = {part: None for part in self.PARTS}
        self._lit: str | None = None

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, store.ENCODER_SIZE, store.ENCODER_SIZE + 24)

    def _circle(self) -> QRectF:
        return QRectF(2, 2, store.ENCODER_SIZE - 4, store.ENCODER_SIZE - 4)

    def _inner(self) -> QRectF:
        c = self._circle()
        d = c.width() * 0.42
        return QRectF(c.center().x() - d / 2, c.center().y() - d / 2, d, d)

    def part_at(self, pos: QPointF) -> str | None:
        c = self._circle().center()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        dist = math.hypot(dx, dy)
        if dist <= self._inner().width() / 2:
            return "press"
        if dist <= self._circle().width() / 2:
            return "left" if dx < 0 else "right"
        return None

    def set_data(self, name: str, keys: dict[str, str | None], captions: dict[str, str]) -> None:
        self.name, self.keys, self.captions = name, keys, captions
        self.update()

    def light(self, part: str | None) -> None:
        self._lit = part
        self.update()

    def paint(self, p: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        circle, inner = self._circle(), self._inner()
        cx = circle.center().x()

        for part, start in (("left", 90), ("right", -90)):
            path = QPainterPath()
            path.moveTo(circle.center())
            path.arcTo(circle, start, 180)
            path.closeSubpath()
            fill = theme.FLASH if self._lit == part else (theme.KEY_HOVER if self._hover else theme.KEY)
            p.setBrush(QColor(fill))
            p.setPen(QPen(QColor(theme.BORDER), 1.5))
            p.drawPath(path)
        p.setBrush(QColor(theme.FLASH if self._lit == "press" else theme.PANEL))
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.drawEllipse(inner)

        # стрелки — в верхней части половинок, подписи — в нижней, где половинки шире
        half_w = circle.width() / 2
        for part, arrow, x0 in (("left", "⟲", circle.left()), ("right", "⟳", cx)):
            lit = self._lit == part
            dark = "#1b1d23"
            _draw_text(p, QRectF(x0, circle.top() + 14, half_w, inner.top() - circle.top() - 14),
                       arrow, dark if lit else theme.SUBTLE, 15)
            cap_rect = QRectF(x0 + (14 if part == "left" else 4), inner.bottom() - 6,
                              half_w - 18, circle.bottom() - inner.bottom() - 14)
            _draw_text(p, cap_rect, self.captions.get(part) or "—", dark if lit else theme.TEXT,
                       8.5, bold=True, min_size=6)
        press = self.captions.get("press") if self.keys.get("press") else "нет"
        _draw_text(p, inner.adjusted(4, 4, -4, -4), press or "—",
                   "#1b1d23" if self._lit == "press" else theme.TEXT, 8, min_size=5.5)
        _draw_text(p, QRectF(0, store.ENCODER_SIZE + 2, store.ENCODER_SIZE, 20), self.name,
                   theme.SUBTLE, 9)


class Board(QGraphicsView):
    """Поле. Сигналы: клик по кнопке/крутилке, перемещение, контекстное меню."""
    key_clicked = Signal(str)
    encoder_clicked = Signal(str, str)          # id, часть
    layout_changed = Signal()
    key_menu = Signal(str, QPoint)
    encoder_menu = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor(theme.BG)))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setAlignment(Qt.AlignCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self._keys: dict[str, KeyItem] = {}
        self._encoders: dict[str, EncoderItem] = {}
        self._doc: dict | None = None

    # --- построение из документа ---
    def set_document(self, doc: dict) -> None:
        self._doc = doc
        scene = self.scene()
        scene.clear()
        self._keys.clear()
        self._encoders.clear()
        for key, pos in doc["layout"]["keys"].items():
            item = KeyItem(key)
            item.setPos(pos.get("x", 0), pos.get("y", 0))
            item.clicked.connect(lambda _pt, k=key: self.key_clicked.emit(k))
            item.moved.connect(lambda k=key: self._key_moved(k))
            item.context_requested.connect(lambda pt, k=key: self.key_menu.emit(k, pt))
            scene.addItem(item)
            self._keys[key] = item
        for enc in doc["layout"]["encoders"]:
            eid = enc["id"]
            item = EncoderItem(eid)
            item.setPos(enc.get("x", 0), enc.get("y", 0))
            item.clicked.connect(lambda pt, i=item: self._encoder_clicked(i, pt))
            item.moved.connect(lambda e=eid: self._encoder_moved(e))
            item.context_requested.connect(lambda pt, e=eid: self.encoder_menu.emit(e, pt))
            scene.addItem(item)
            self._encoders[eid] = item
        self.refresh_texts()
        self._fit()

    def refresh_texts(self) -> None:
        doc = self._doc
        for key, item in self._keys.items():
            pos = doc["layout"]["keys"].get(key, {})
            item.set_texts(pos.get("label") or bindtext.pretty_key(key),
                           bindtext.label(doc["binds"].get(key)))
        for enc in doc["layout"]["encoders"]:
            item = self._encoders.get(enc["id"])
            if item is None:
                continue
            keys = {part: enc.get(part) for part in EncoderItem.PARTS}
            captions = {part: bindtext.label(doc["binds"].get(k)) if k else ""
                        for part, k in keys.items()}
            item.set_data(enc.get("name", ""), keys, captions)

    def _fit(self) -> None:
        rect = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.scene().setSceneRect(rect.united(QRectF(-40, -40, 400, 300)))

    # --- события элементов ---
    def _key_moved(self, key: str) -> None:
        item = self._keys[key]
        pos = self._doc["layout"]["keys"].setdefault(key, {})
        pos["x"], pos["y"] = int(item.x()), int(item.y())
        self._fit()
        self.layout_changed.emit()

    def _encoder_moved(self, enc_id: str) -> None:
        item = self._encoders[enc_id]
        for enc in self._doc["layout"]["encoders"]:
            if enc["id"] == enc_id:
                enc["x"], enc["y"] = int(item.x()), int(item.y())
        self._fit()
        self.layout_changed.emit()

    def _encoder_clicked(self, item: EncoderItem, pt: QPointF) -> None:
        self.encoder_clicked.emit(item.enc_id, item.part_at(pt) or "left")

    # --- подсветка физических нажатий ---
    def light_key(self, key: str, on: bool) -> bool:
        """Подсветить элемент для клавиши. False — такой клавиши на поле нет."""
        if key in self._keys:
            self._keys[key].light(on)
            return True
        found = store.find_encoder(self._doc, key) if self._doc else None
        if found:
            enc, part = found
            item = self._encoders.get(enc["id"])
            if item:
                item.light(part if on else None)
                return True
        return False

    # --- фон и пустое состояние ---
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        painter.setPen(QPen(QColor(theme.GRID), 1))
        step = store.GRID_STEP / 2
        x = math.floor(rect.left() / step) * step
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = math.floor(rect.top() / step) * step
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self._keys or self._encoders:
            return
        painter.resetTransform()
        _draw_text(painter, QRectF(self.viewport().rect()),
                   "Поле пустое.\n\nНажмите «＋ Кнопка» и нажимайте кнопки на мини-клавиатуре —\n"
                   "они появятся здесь. Потом перетащите их, как они расположены на устройстве.",
                   theme.SUBTLE, 11)
