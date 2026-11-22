"""Clear pyqtgraph's process-wide ViewBox registry before Qt teardown.

pyqtgraph keeps every ViewBox in class-level registries
(``ViewBox.AllViews`` / ``ViewBox.NamedViews``). A *named* ViewBox (every
``ImageView`` registers one) connects its Qt ``destroyed`` signal to
``ViewBox.forgetView()``, which rebuilds the "link axis" combo boxes in the
context menu of every other registered ViewBox. At Qt teardown those combo
boxes can already be deleted on the C++ side while their ViewBox is still in
the registry, and pyqtgraph then prints::

    RuntimeError: wrapped C/C++ object of type QComboBox has been deleted

Emptying both registries once no more displays will be shown leaves
``forgetView()`` nothing to rebuild. The only thing lost is the right-click
axis-link list, which nothing in this application uses.
"""

from pyqtgraph.graphicsItems.ViewBox import ViewBox


def clear_view_registry():
    """Forget every registered ViewBox; safe to call more than once."""
    ViewBox.AllViews.clear()
    ViewBox.NamedViews.clear()
