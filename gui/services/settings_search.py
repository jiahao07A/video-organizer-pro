"""用界面定义补全设置索引；不读取输入值、凭据或动态状态文本。"""
import re
from PySide6.QtWidgets import QWidget, QFormLayout, QGroupBox, QCheckBox, QPushButton, QLabel
from gui.models.settings_catalog import SettingsEntry, SETTINGS_ENTRIES, MANUAL


def build_search_index(view):
    entries = []
    view._search_tab_by_key = {}
    seen = set()

    def add(label, widget, tab, *, key=None, category=None, keywords=()):
        if not isinstance(widget, QWidget):
            return
        label = re.sub(r"<[^>]*>", "", label).strip(" :：·")
        identity = (label, tab)
        if not label or identity in seen:
            return
        seen.add(identity)
        key = key or "ui_search_" + str(len(entries))
        view._search_widget_map[key] = widget
        view._search_tab_by_key[key] = tab
        category = category or {0:"analysis",1:"ai",2:"prompts",3:"everyday",4:"naming",5:"everyday"}[tab]
        entries.append(SettingsEntry(key,key,label,category,tuple(keywords),MANUAL,"",tab))

    for entry in SETTINGS_ENTRIES:
        widget = view._search_widget_map.get(entry.widget_key)
        if isinstance(widget, QWidget):
            tab = next((i for i in range(view.tabs.count()) if view.tabs.widget(i).isAncestorOf(widget)), entry.tab_index)
            add(entry.label,widget,tab,key=entry.widget_key,category=entry.category_id,keywords=entry.keywords+(entry.key,entry.effect))
    from gui.widgets.recovery_controls import FIELD_LABELS
    for field,label in FIELD_LABELS.items():
        add(label,view.http400_settings.global_editor.fields[field],0,keywords=("400",field,"兜底","恢复"))
    for tab in range(view.tabs.count()):
        page = view.tabs.widget(tab)
        for form in page.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row,QFormLayout.LabelRole)
                field_item = form.itemAt(row,QFormLayout.FieldRole)
                label = label_item.widget() if label_item else None
                if not isinstance(label,QLabel) or field_item is None:
                    continue
                def first_widget(item):
                    if item.widget():
                        return item.widget()
                    layout = item.layout()
                    if layout:
                        for i in range(layout.count()):
                            found = first_widget(layout.itemAt(i))
                            if found and not isinstance(found,QLabel):
                                return found
                    return None
                add(label.text(),first_widget(field_item),tab)
        for group in page.findChildren(QGroupBox):
            add(group.title(),group,tab)
        for check in page.findChildren(QCheckBox):
            if check.text()!="沿用上级":
                add(check.text(),check,tab)
        for button in page.findChildren(QPushButton):
            if len(button.text())>3:
                add(button.text(),button,tab)
    # 非表单布局的提示词与路由，以及折叠的400表格字段。
    local = list(getattr(view,"_local_prompt_edits",{}).values())
    if local:
        add("标签组引导词",local[0],2,key="local_prompt",keywords=("local_prompt","局部提示词"))
    prompts = list(getattr(view,"_tag_ai_prompt_edits",{}).values())
    if prompts:
        add("标签库 AI 模板",prompts[0],2,key="tag_ai_prompts",keywords=("待审","近义","标准词"))
    for name,widget in getattr(view,"_tag_ai_prompt_edits",{}).items():
        label = {"pending_tag_ai":"待审词 AI（含批量分拣）", "synonym_audit":"近义巡检", "standard_tag_ai":"标准词 AI 助手"}.get(name,name)
        add(label,widget,2,keywords=(name,"提示词","模板"))
    from core.model_providers import TASK_ROUTE_LABELS
    for task,widgets in view._route_widgets.items():
        add(TASK_ROUTE_LABELS.get(task,task)+" 模型",widgets["model"],1,keywords=(task,"路由","模型"))
    return tuple(entries)
