"""视频主分析的结果契约；新响应、缓存和文件操作共用同一失败判定。"""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Annotated, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from core.analysis_targets import is_failed_status


class InvalidAnalysisResult(ValueError):
    """固定错误信息不含响应正文，避免将模型输出写进诊断日志。"""

    def __init__(self, reason: str):
        super().__init__(f"invalid analysis result: 无效分析结果，{reason}")


Text = Annotated[str, Field(min_length=1)]
GroupValue = str | list[str] | None
_group_value = TypeAdapter(GroupValue)


class _AnalysisResult(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow', str_strip_whitespace=True)

    category: Text
    summary: Text
    tags: list[Text] = Field(default_factory=list)
    tag_groups: dict[str, GroupValue] = Field(default_factory=dict)
    success: bool | None = None
    ok: bool | None = None
    status: str | None = None


# 只匹配模型自述无法完成任务；不在任意画面描述中搜索“失败”等泛化词。
_FAILURE_SUMMARY = re.compile(
    r"^(?:(?:抱歉|很抱歉|对不起)[，,。.!！:：\s]*)?(?:"
    r"(?:我|我们|本模型)?(?:目前|暂时)?(?:抽帧失败(?=$|[，,。.;；:：\s])|"
    r"(?:视频|图像|图片)帧?(?:提取|抽取|获取|读取|解码|解析)失败(?=$|[，,。.;；:：\s])|"
    r"(?:无法|未能|不能)(?:从[^，。；\n]{0,24})?(?:提取|抽取|获取|读取|解码|解析|识别|查看|看到|访问|分析|处理)"
    r"(?:到)?(?:所提供的|提供的|输入的|该|此|这些|任何|有效的|有效|清晰的|清晰|相关的|相关)?(?:视频|图像|图片|画面|帧|影像)|"
    r"(?:无法|未能)(?:完成)?(?:视频|图像|图片|画面)(?:内容)?的?(?:分析|识别|读取|解析)|"
    r"(?:未提供|没有提供|缺少)(?:可供分析的|有效的|有效|可用的)?(?:视频帧|图像|图片))|"
    r"(?:frame|image|video frame)\s+(?:extraction|decoding|analysis)\s+failed\b|"
    r"(?:sorry[,.:\s]*|i(?:'m| am) sorry[,.:\s]*)?"
    r"(?:(?:i|we)\s+)?(?:cannot|can't|could not|couldn't|unable to|failed to)\s+"
    r"(?:extract|read|access|view|see|analy[sz]e|process|decode|identify)\b[^.!?\n]{0,100}"
    r"\b(?:video|frames?|images?|pictures?|visual)\b"
    r")", re.IGNORECASE,
)
_EMPTY_SUMMARIES = frozenset({'未知', '无法判断', '无法识别', '无法分析', '无有效内容',
                              'unknown', 'n/a', 'none', 'null'})
_FAILED_STATES = frozenset({'error', 'failed', 'failure', 'refused', 'unsupported', '失败', '分析失败'})
_SELF_REPORT_START = re.compile(r"^(?:抱歉|很抱歉|对不起|我|我们|本模型|sorry\b|i\b|we\b)", re.IGNORECASE)
# 失败短语作为屏幕提示的主语时属于画面描述；只接受紧接前缀的明确显示语法。
_VISIBLE_ERROR_SUFFIX = re.compile(
    r"^(?:的)?(?:错误)?(?:提示|消息|信息|文字|字样)(?:框)?(?:正|正在)?(?:被)?"
    r"(?:显示|展示|呈现|出现|位于)[^。！？;；\n]{0,24}(?:屏幕|界面|显示器)|"
    r"^\s+(?:(?:error|warning)\s+)?(?:(?:message|text|notice|warning)\s+)?"
    r"(?:(?:is|was|can be)\s+)?(?:displayed|shown|appears?|visible)\b"
    r"[^.!?\n]{0,60}\b(?:screen|display|monitor|window|dialog)\b",
    re.IGNORECASE,
)


def reports_analysis_failure(value: Any, *, group_ids: Iterable[str] = ()) -> bool:
    """兼容旧数据库记录：只判定明确失败，不要求旧人工记录补齐新响应字段。"""
    if not isinstance(value, dict):
        return False
    group_ids = frozenset(group_ids)
    if any(key not in group_ids and value.get(key) is False for key in ('success', 'ok')):
        return True
    if any(key not in group_ids and value.get(key) for key in ('error', 'error_message', 'refusal')):
        return True
    state = value.get('status')
    if 'status' not in group_ids and isinstance(state, str) and state.strip().lower() in _FAILED_STATES:
        return True
    summary = value.get('summary')
    if not isinstance(summary, str):
        return False
    summary = unicodedata.normalize('NFKC', summary).replace('\u200b', '').replace('\ufeff', '').strip()
    if summary.lower().rstrip('。.!！') in _EMPTY_SUMMARIES:
        return True
    failure = _FAILURE_SUMMARY.match(summary)
    if not failure:
        return False
    return not (not _SELF_REPORT_START.match(summary)
                and _VISIBLE_ERROR_SUFFIX.match(summary[failure.end():]))


def validate_analysis_result(value: Any, *, group_ids: Iterable[str] = ()) -> dict:
    """校验响应并返回独立副本；允许现有单选字符串与多选标签结构。"""
    group_ids = frozenset(group_ids)
    if reports_analysis_failure(value, group_ids=group_ids):
        raise InvalidAnalysisResult('模型报告未完成视觉分析，请检查图片识别能力或重新分析')
    try:
        # 配置中的同名动态组仍按标签校验，不能被新增协议状态字段抢占。
        protocol_groups = group_ids.intersection(('success', 'ok', 'status'))
        payload = {k: v for k, v in value.items() if k not in protocol_groups} if isinstance(value, dict) else value
        result = _AnalysisResult.model_validate(payload)
        for group_id in group_ids:
            if group_id in value:
                _group_value.validate_python(value[group_id], strict=True)
    except ValidationError:
        raise InvalidAnalysisResult('分类、摘要或标签字段缺失或类型错误') from None
    # 深复制原始有效结果，避免给旧缓存或响应添加不存在的状态标记。
    data = copy.deepcopy(value)
    data['category'] = result.category
    data['summary'] = result.summary
    return data


def ensure_rename_analysis(item: dict, pattern: str) -> None:
    """旧错误结果仅在使用分析字段时拦截，原名/日期整理仍然可用。"""
    if not any('{' + key + '}' in pattern for key in ('category', 'tags', 'summary')):
        return
    # 业务状态 failed 可能来自重新分析失败，不能覆盖仍然有效的旧成果。
    metadata = item.get('raw_metadata')
    # 已归一保存的组名保留历史身份，即使当前配置已删除该组也不能误判为错误状态。
    stored_groups = item.get('tag_groups')
    group_ids = stored_groups.keys() if isinstance(stored_groups, dict) else ()
    analysis = {key: item.get(key) for key in ('category', 'summary', 'tags')}
    failed_without_correction = is_failed_status(item.get('status')) and not item.get('manual_override')
    if (failed_without_correction or reports_analysis_failure(analysis)
            or (not item.get('manual_override') and isinstance(metadata, dict)
                and reports_analysis_failure(metadata, group_ids=group_ids))):
        raise InvalidAnalysisResult('已保存的分析结果包含失败说明，已阻止重命名；请强制重新分析或人工修正摘要')
