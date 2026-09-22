'''Model-assisted planning for domain-neutral smart-table composition.'''
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-v4-pro'
REQUEST_TIMEOUT = 90.0


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = (raw or '').strip()
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = text.removeprefix(fence + 'json').removeprefix(fence).removesuffix(fence).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _call_json(config: dict[str, str] | None, system: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    config = config or {}
    key = str(config.get('api_key') or '').strip()
    if not key:
        return None, '模型未配置。当前仅提供基础制表方向，不会假装已经理解业务需求。'
    base_url = str(config.get('base_url') or DEFAULT_BASE_URL).strip()
    model = str(config.get('model') or DEFAULT_MODEL).strip()
    try:
        client = OpenAI(api_key=key, base_url=base_url, timeout=REQUEST_TIMEOUT)
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ],
        )
        parsed = _extract_json(response.choices[0].message.content or '')
        return (parsed, '') if parsed is not None else (None, '模型返回内容无法通过结构化解析。')
    except Exception as exc:  # noqa: BLE001
        message = str(exc).strip().replace('\n', ' ')
        if len(message) > 180:
            message = message[:177] + '...'
        return None, f'模型规划暂不可用：{message or type(exc).__name__}'


def _field_profile(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for field in dataset.get('fields', []):
        role = field.get('role')
        top_values = []
        if int(field.get('unique_count', 0)) <= 40:
            top_values = [item.get('value') for item in field.get('top_values', [])[:8]]
        examples = []
        if role == 'date':
            examples = ['<DATE>'] if field.get('examples') else []
        elif role == 'amount' or field.get('data_type') == 'number':
            examples = ['<NUMBER>'] if field.get('examples') else []
        elif top_values:
            examples = top_values
        else:
            examples = [f'<VALUE_{index + 1}>' for index, _ in enumerate(field.get('examples', [])[:3])]
        result.append({
            'name': field.get('name'),
            'suggested_role': role,
            'role_evidence': field.get('confidence'),
            'data_type': field.get('data_type'),
            'null_rate': field.get('null_rate'),
            'nonempty_count': field.get('nonempty_count'),
            'unique_count': field.get('unique_count'),
            'unique_count_exact': field.get('unique_count_exact'),
            'positive_count': field.get('positive_count'),
            'negative_count': field.get('negative_count'),
            'zero_count': field.get('zero_count'),
            'representative_values': examples,
            'warnings': field.get('warnings', []),
        })
    return result


def _context(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        'file_count': dataset.get('file_count'),
        'sheet_count': dataset.get('sheet_count'),
        'total_rows': dataset.get('total_rows'),
        'sheets': [
            {
                'file': item.get('file'),
                'sheet': item.get('sheet'),
                'columns': item.get('columns', []),
                'rows': item.get('rows'),
            }
            for item in dataset.get('sheets', [])[:30]
        ],
        'fields': _field_profile(dataset),
        'candidate_keys': dataset.get('modeling_evidence', {}).get('candidate_keys', []),
        'quality_findings': dataset.get('quality_findings', []),
        'field_relationships': dataset.get('evidence', {}).get('fields', {}),
        'transaction_evidence': dataset.get('evidence', {}).get('transaction', {}),
    }


PLAN_PROTOCOL = '''
table 是唯一可执行计划，结构如下。
明细表：mode=detail，output_name，filters，columns，sort，limit。
汇总表：mode=aggregate，output_name，filters，group_by，aggregations，可选 columns，sort，limit。
filters 每项使用真实 field，operator 仅限 equals/not_equals/contains/not_contains/in/not_in/greater_than/greater_or_equal/less_than/less_or_equal/is_empty/not_empty，value 为比较值。
columns 每项为 name 和 expression。group_by 每项为 field 和 name。aggregations 每项为 name、aggregation 和 expression；aggregation 仅限 sum/average/min/max/count/count_distinct，count 可省略 expression。
sort 每项为最终输出字段 field 和 asc/desc。
expression 必须是受控表达式树：field、literal、add、subtract、multiply、divide、abs、negate、coalesce、concat、if。
field 使用 op=field 和真实 field；literal 使用 op=literal 和 value；运算使用 op 和 args；if 使用 condition、then、else。
filters 和 if.condition 使用完全相同的条件结构：field、operator、value；不要输出 left/right 条件表达式。
汇总表要展示名称或标签字段时，将该字段放入 group_by；columns 只能引用分组和聚合后的输出字段，不能直接引用源字段。
汇总表使用 columns 时必须显式保留全部 group_by 输出字段，不能只输出指标而丢失分组标识。
若 transaction_evidence 证明退货金额本身为负数：可以用 abs 转正展示并以销售额减正退货额计算净额，或保留负数并以销售额加负退货额计算净额；严禁销售额减负退货额。
不得输出 Python、SQL、公式字符串或协议之外的操作。
'''.strip()


def generate_directions(dataset: dict[str, Any], config: dict[str, str] | None) -> tuple[list[dict[str, Any]] | None, str]:
    system = f'''
你是表速通的数据建模规划器。用户只有源表，没有目标样例，也暂未说明业务目标。根据紧凑数据画像设计三个证据最充分、彼此不同、可以作为新目标表的方向。
重点判断结果粒度、筛选、保留字段、派生字段、分组、指标和排序。不要局限于销售汇总；人员、工资、库存、考勤、资产、采购等都使用同一协议。不要生成经营分析、图表、预测或原因结论。除非方向明确是 Top N，否则 limit 必须为 null。
三个方向不能只是更换分组字段：至少在明细/汇总模式、筛选范围、结果粒度、指标公式或目标用途中的两项存在实质差异。优先覆盖“主体汇总、业务过程明细、时间或责任维度建模”等源数据确实支持的不同用途。
规则和字段画像只是事实证据，不能替用户决定真实业务含义。方向中存在业务歧义时写入 warnings。只有完整符合下方协议且使用真实字段的方向才能返回。
{PLAN_PROTOCOL}
只输出 JSON 对象，顶层为 directions 数组，最多三个。每项包含 title、description、evidence、outputs、recommended、warnings、plan_hint；evidence、outputs、warnings 必须是字符串数组；plan_hint 必须为 output_kind=table 和 table。推荐项最多一个。不要输出概率置信度。
'''.strip()
    data, error = _call_json(config, system, {'dataset': _context(dataset)})
    if not data or not isinstance(data.get('directions'), list):
        return None, error
    return data['directions'][:3], error


def supplement_directions(
    dataset: dict[str, Any],
    accepted: list[dict[str, Any]],
    validation_errors: list[str],
    missing_count: int,
    config: dict[str, str] | None,
) -> tuple[list[dict[str, Any]] | None, str]:
    if missing_count <= 0:
        return [], ''
    system = f'''
你是表速通的数据建模计划修正器。首轮候选中有计划未通过受控协议校验或与其他方案实质重复。请补充 {missing_count} 个新的、完整可执行的目标表方向。
新方向必须与 accepted_directions 在明细/汇总模式、筛选范围、结果粒度、指标公式或目标用途中的至少两项不同，不能只更换分组字段。只依据数据画像能证明的信息，不生成经营分析、图表、预测或原因结论。
严格修正 validation_errors 中的问题；每个方向都必须包含完整 table，不能把 table 放进 outputs，也不能返回旧版 output_kind。
{PLAN_PROTOCOL}
只输出 JSON 对象，顶层为 directions 数组。每项包含 title、description、evidence、outputs、recommended=false、warnings、plan_hint；evidence、outputs、warnings 必须是字符串数组；plan_hint 必须为 output_kind=table 和 table。
'''.strip()
    compact_accepted = [
        {
            'title': item.get('title'),
            'description': item.get('description'),
            'table': (item.get('plan_hint') or {}).get('table'),
        }
        for item in accepted[:3]
    ]
    payload = {
        'dataset': _context(dataset),
        'accepted_directions': compact_accepted,
        'validation_errors': validation_errors[:8],
        'missing_count': missing_count,
    }
    data, error = _call_json(config, system, payload)
    if not data or not isinstance(data.get('directions'), list):
        return None, error
    return data['directions'][:missing_count], error


def refine_plan(
    dataset: dict[str, Any],
    plan: dict[str, Any],
    message: str,
    facts: list[dict[str, Any]],
    config: dict[str, str] | None,
) -> tuple[dict[str, Any] | None, str]:
    system = f'''
你是表速通的目标表需求建模器。把用户要求转换为一份完整、受控、可验证的目标表计划。每次都返回完整 table，不返回增量片段。user_message 是本轮最高优先级要求，必须逐项落实其中的筛选、输出字段、公式和排序；不得用当前占位方案覆盖用户明确要求，也不得自行增加用户未要求的输出列。
先用具体中文 requirement_summary 复述：每行代表什么、保留哪些记录、输出字段、计算公式、分组聚合和排序。不得把未说明的口径当成事实。
缺少且会改变结果的信息放入 questions，每个问题必须具体且 blocking=true。当前协议不能表达的要求放入 unresolved_requirements，不能静默忽略。模型只规划；最终数值由程序基于全量源数据计算。
{PLAN_PROTOCOL}
只输出 JSON 对象，字段为 direction_title、requirement_summary、table、questions、unresolved_requirements、assistant_message。
'''.strip()
    compact_plan = {
        'direction_title': plan.get('direction_title'),
        'requirement_summary': plan.get('requirement_summary'),
        'table': None if plan.get('direction_id') == 'custom_requirement' and len(facts) <= 1 else plan.get('table'),
        'confirmed_decisions': plan.get('decision_log', [])[-20:],
        'pending_questions': plan.get('questions', []),
    }
    payload = {
        'dataset': _context(dataset),
        'current_plan': compact_plan,
        'confirmed_facts': facts[-20:],
        'user_message': message,
    }
    return _call_json(config, system, payload)


def repair_plan(
    dataset: dict[str, Any],
    attempted: dict[str, Any],
    original_message: str,
    gaps: list[str],
    facts: list[dict[str, Any]],
    config: dict[str, str] | None,
) -> tuple[dict[str, Any] | None, str]:
    system = f'''
你是表速通的目标表计划修正器。上一版计划没有完整落实用户的明确要求。只修正列出的缺口，并返回一份完整、受控、可验证的 table。
原始用户要求优先级最高。不得省略筛选、不得增加未要求的直接输出字段、不得改变明确排序和公式。requirement_summary 必须忠实描述修正后的 table。
{PLAN_PROTOCOL}
只输出 JSON 对象，字段为 direction_title、requirement_summary、table、questions、unresolved_requirements、assistant_message。
'''.strip()
    payload = {
        'dataset': _context(dataset),
        'original_user_message': original_message,
        'validation_gaps': gaps,
        'attempted_plan': attempted,
        'confirmed_facts': facts[-20:],
    }
    return _call_json(config, system, payload)
