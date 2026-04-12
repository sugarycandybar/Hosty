from PySide6.QtWidgets import QScrollArea
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, Property

class SmoothScrollArea(QScrollArea):
    """A QScrollArea that provides smooth, animated scrolling for wheel events."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_anim = QVariantAnimation(self)
        self._scroll_anim.setDuration(400)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._scroll_anim.valueChanged.connect(self._on_scroll_anim_value_changed)
        
    def _on_scroll_anim_value_changed(self, value):
        self.verticalScrollBar().setValue(value)

    def wheelEvent(self, event):
        # We only handle vertical scrolling for now
        delta = event.angleDelta().y()
        if delta == 0:
            return
            
        scrollbar = self.verticalScrollBar()
        current_value = scrollbar.value()
        
        # If an animation is already running, take its end value as the starting point
        # to allow "stacking" scroll increments.
        if self._scroll_anim.state() == QVariantAnimation.State.Running:
            start_val = self._scroll_anim.endValue()
        else:
            start_val = current_value
            
        # Standard step is usually 120 delta = 3 lines or so. 
        # Minecraft style scroll is usually a bit more pronounced.
        target_val = start_val - delta
        
        # Clamp target
        target_val = max(scrollbar.minimum(), min(target_val, scrollbar.maximum()))
        
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(current_value)
        self._scroll_anim.setEndValue(target_val)
        self._scroll_anim.start()
