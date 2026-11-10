import pytest
from PySide6.QtCore import QObject, QEvent, QPoint, Qt
from PySide6.QtWidgets import QWidget, QScrollArea
from PySide6.QtTest import QTest
from tests.support.display import service as service
from gui.views.settings import SettingsView
from gui.views.workstation import WorkstationView
from gui.views.material_library import MaterialLibraryView

class ShowRecorder(QObject):
    def __init__(self):
        super().__init__()
        self.shown=[]
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Show and isinstance(obj,QWidget) and obj.isWindow():
            self.shown.append(type(obj).__name__)
        return False

def test_settings_construction_does_not_flash_top_level_widgets(app,service):
    recorder=ShowRecorder()
    app.installEventFilter(recorder)
    try:
        view=SettingsView(service)
        assert recorder.shown==[]
        view.close()
    finally:
        app.removeEventFilter(recorder)

@pytest.mark.parametrize("view_type",[WorkstationView,MaterialLibraryView])
def test_columns_fill_available_width_on_window_and_splitter_resize(app,service,view_type):
    view=view_type(service)
    try:
        view.resize(2300,900)
        view.show()
        QTest.qWait(30)
        table=view.table_view
        header=table.horizontalHeader()
        assert abs(header.length()-table.viewport().width())<=3
        saved=dict(service.settings.get("ui_preferences",{}).get("table_column_widths",{}))
        view.splitter.setSizes([1400,600])
        QTest.qWait(30)
        assert abs(header.length()-table.viewport().width())<=3
        assert service.settings.get("ui_preferences",{}).get("table_column_widths",{})==saved
    finally:
        view.close()

@pytest.mark.parametrize("query",["管理数据目录","分类默认","最长等待间隔","不自动重试的错误","自动检查更新","标签组引导词"])
def test_search_finds_existing_settings_and_focuses(app,service,query):
    view=SettingsView(service)
    try:
        view.settings_search_input.setText(query)
        items=[view.settings_search_results.model().index(i,0) for i in range(view.settings_search_results.model().rowCount())]
        valid=[i for i in items if i.data(256)]
        assert valid, query
        assert view.focus_setting(valid[0].data(256))
    finally:
        view.close()


def test_advanced_search_opens_fold_and_preserves_values(app, service):
    view=SettingsView(service)
    try:
        view.resize(1200,850)
        view.show()
        view.settings_search_input.setText("最长等待间隔")
        target=view.settings_search_results.model().index(0,0).data(256)
        editor=view.http400_settings.global_editor
        before=editor.override()
        assert not editor.advanced_toggle.isChecked()
        assert view.focus_setting(target)
        QTest.qWait(80)
        assert editor.advanced_toggle.isChecked()
        assert view._search_widget_map[target].isVisible()
        assert editor.override()==before
        widget=view._search_widget_map[target]
        area=widget.parentWidget()
        while not isinstance(area,QScrollArea):
            area=area.parentWidget()
        point=widget.mapTo(area.viewport(),QPoint(0,0))
        assert 0<=point.y()<area.viewport().height()-widget.height()
    finally:
        view.close()


def test_search_does_not_index_user_input_and_supports_multiple_terms(app,service):
    view=SettingsView(service)
    try:
        view.api_key_input.setText("SECRET_NEVER_INDEX_ME")
        view.settings_search_input.setText("SECRET_NEVER_INDEX_ME")
        assert not view.settings_search_results.model().index(0,0).data(256)
        view.settings_search_input.setText("400 等待")
        assert any(view.settings_search_results.model().index(i,0).data(256) for i in range(view.settings_search_results.model().rowCount()))
    finally:
        view.close()


def test_search_dropdown_preserves_settings_space_and_keyboard_selection(app,service):
    view=SettingsView(service)
    try:
        view.resize(1300,900)
        view.show()
        QTest.qWait(20)
        before=view.tabs.geometry()
        view.settings_search_input.setFocus()
        view.settings_search_input.setText("最长等待间隔")
        QTest.qWait(30)
        assert view.settings_search_results.isVisible()
        assert view.settings_search_results.isWindow()
        assert view.tabs.geometry()==before
        QTest.keyClick(view.settings_search_input,Qt.Key_Down)
        QTest.keyClick(view.settings_search_results,Qt.Key_Return)
        QTest.qWait(80)
        assert not view.settings_search_results.isVisible()
        assert view.http400_settings.global_editor.advanced_toggle.isChecked()
        assert view.tabs.geometry()==before
        view.settings_search_input.setFocus()
        view.settings_search_input.setText("400")
        QTest.qWait(20)
        QTest.keyClick(view.settings_search_results,Qt.Key_Escape)
        assert not view.settings_search_results.isVisible()
        view.settings_search_input.setFocus()
        view.settings_search_input.setText("主题")
        QTest.qWait(20)
        popup=view.settings_search_results
        first=popup.model().index(0,0)
        QTest.mouseClick(popup.viewport(),Qt.LeftButton,Qt.NoModifier,popup.visualRect(first).center())
        QTest.qWait(30)
        assert view.tabs.currentIndex()==3
        assert not popup.isVisible()
    finally:
        view.close()


def test_manual_column_drag_rebalances_remaining_columns(app, service):
    view=WorkstationView(service)
    try:
        view.resize(1800, 850)
        view.show()
        QTest.qWait(50)
        header=view.table_view.horizontalHeader()
        before=sum(header.sectionSize(i) for i in range(view.model.columnCount()) if not view.table_view.isColumnHidden(i))
        old=header.sectionSize(3)
        header.resizeSection(3, old+120)
        QTest.qWait(50)
        after=sum(header.sectionSize(i) for i in range(view.model.columnCount()) if not view.table_view.isColumnHidden(i))
        assert header.sectionSize(3) >= old+100
        assert abs(after-header.length()) <= 2
    finally:
        view.close()
