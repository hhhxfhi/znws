"""Explicit administrator review of real questions; feedback is never truth."""
import copy
import hashlib
import time
import uuid
from .catalog_index import fingerprint
from natural_work.store import ConflictError

CATEGORIES = {'scope': '范围', 'relation': '关联', 'retrieval': '检索', 'term': '术语', 'delivery': '交付'}


def _publish_review_artifact(engine, scope, subject, record, replace_id=None, idempotency_key=None):
    """Publish a review record to the artifact with optimistic concurrency retry and idempotency."""
    for attempt in range(5):
        latest_rev, latest_head = engine.artifacts.head(scope, subject, 'data.configuration-review')
        current_records = list(latest_head['payload']['records']) if latest_head else []

        # If idempotency_key is provided, check if matching record already exists
        if idempotency_key:
            duplicate = next((r for r in current_records if r.get('idempotency_key') == idempotency_key), None)
            if duplicate:
                return duplicate, current_records

        target_id = replace_id or record.get('id')
        existing_idx = next((i for i, r in enumerate(current_records)
                             if r.get('id') == target_id or
                             (r.get('kind') == record.get('kind') == 'staged_example' and r.get('review_id') == record.get('review_id'))), None)

        if existing_idx is not None:
            current_records[existing_idx] = record
        else:
            current_records.append(record)

        try:
            engine.artifacts.publish(
                scope, subject, 'data.configuration-review',
                {'records': current_records},
                expected_revision=latest_rev,
                producer='data.configuration-review@0.8.0',
                parents=[]
            )
            return record, current_records
        except ConflictError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def review_action(admin, actor, args, host):
    business, engine = admin.business, admin.engine
    cfg = business.config()
    bid = args.get('business_project')
    project = business.require(cfg, actor, bid, 'editor')
    action = args.get('action')
    sessions = {key[len(actor) + 1:]: value for key, value in cfg['sessions'].items()
                if key.startswith(actor + ':') and value.get('business_project') == bid}

    if action == 'review_sessions':
        return {'session_ids': [key for key in sessions if key.startswith('session-')][-50:]}

    sid = args.get('session_id')
    if action == 'authorize_session':
        if sid not in sessions:
            raise PermissionError('会话未绑定到当前用户的该业务项目。')
        return {'authorized': True}

    business.session(actor, 'admin-review:' + bid, bid, 'admin-review/' + bid)
    scope = business.resolve(actor, 'admin-review/' + bid, 'admin-review:' + bid)
    subject = 'configuration-review:' + bid
    revision, head = engine.artifacts.head(scope, subject, 'data.configuration-review')
    records = head['payload']['records'] if head else []

    if action == 'review_history':
        return {'records': records}

    if action == 'capture_review':
        review = host.get('session_review') if isinstance(host, dict) else None
        if not isinstance(review, dict) or review.get('session_id') not in sessions:
            raise PermissionError('需要宿主读取的本项目会话证据。')
        if args.get('category') not in CATEGORIES:
            raise ValueError('请选择范围、关联、检索、术语或交付问题。')
        note_text = str(args.get('note', '')).strip()
        if not note_text:
            raise ValueError('请说明需要纠正的行为；反馈不等同于独立真值。')

        result_scope = business.resolve(actor, review['workspace'], review['session_id'])
        candidates = []
        for ref in review.get('result_refs', [])[:100]:
            try:
                result = engine.get(result_scope, ref)
            except (ValueError, PermissionError):
                continue
            if result.get('purpose') == 'explore':
                continue
            candidates.append({
                'result_ref': ref,
                'source_id': result['source_id'],
                'sql': result.get('sql', ''),
                'status': result['status']
            })

        # Stable content fingerprint: distinguishes identical duplicate clicks from new feedback notes
        content_key = f"{review.get('session_id')}:{review.get('turn')}:{args.get('category')}:{note_text}"
        idempotency_key = args.get('idempotency_key') or hashlib.sha256(content_key.encode()).hexdigest()

        # Check existing records in the current head
        existing_dup = next((r for r in records if r.get('idempotency_key') == idempotency_key), None)
        if existing_dup:
            return {'record': existing_dup}

        record_id = uuid.uuid4().hex
        record = {
            'id': record_id,
            'idempotency_key': idempotency_key,
            'kind': 'session_feedback',
            'category': args['category'],
            'note': note_text[:4000],
            **review,
            'candidates': candidates,
            'at': time.time(),
            'state': '待复核',
            'answer_match': None
        }
        published_rec, _ = _publish_review_artifact(engine, scope, subject, record, replace_id=record_id, idempotency_key=idempotency_key)
        return {'record': published_rec}

    elif action == 'stage_review_example':
        original = next((r for r in records if r['id'] == args.get('review_id') and r.get('kind') == 'session_feedback'), None)
        if not original:
            raise ValueError('待复核问题不存在。')

        expected = args.get('expected_rows')
        if expected is not None and (not isinstance(expected, list) or not all(isinstance(row, dict) for row in expected)):
            raise ValueError('独立预期需要结果行对象数组。')

        draft = project['draft']
        existing_ex = next((e for e in draft.get('examples', []) if e.get('origin_review') == original['id']), None)
        existing_staged = next((r for r in records if r.get('kind') == 'staged_example' and r.get('review_id') == original['id']), None)

        incoming_sql = args.get('logical_sql', '')
        incoming_assumptions = str(args.get('assumptions', ''))[:4000]
        incoming_source = args.get('source_id')

        # Check for exact duplicate retry of an already staged example
        is_exact_duplicate = (
            existing_ex is not None and
            existing_ex.get('source_id') == incoming_source and
            existing_ex.get('logical_sql') == incoming_sql and
            existing_ex.get('expected_rows') == expected and
            existing_ex.get('assumptions', '') == incoming_assumptions
        )

        if is_exact_duplicate:
            # Idempotent retry: draft already reflects this exact staged example.
            # Do not mutate draft, do not fail on expected_revision changed by intervening work.
            record_id = existing_staged['id'] if existing_staged else uuid.uuid4().hex
            record = {
                'id': record_id,
                'kind': 'staged_example',
                'review_id': original['id'],
                'example_id': existing_ex['id'],
                'question': original['question'],
                'at': existing_staged['at'] if existing_staged else time.time(),
                'state': '待复核草稿',
                'expected_fingerprint': fingerprint(expected) if expected is not None else None
            }
            published_rec, _ = _publish_review_artifact(engine, scope, subject, record, replace_id=record_id)
            return {'record': published_rec}

        # For new staging or modifications to existing example, require strict expected_revision check
        if args.get('expected_revision') != cfg['revision']:
            raise ConflictError('配置已变化，请刷新后重试。')

        # Deep copy to prepare new draft state
        new_draft = copy.deepcopy(draft)
        target_ex = next((e for e in new_draft.get('examples', []) if e.get('origin_review') == original['id']), None)

        if target_ex:
            example_id = target_ex['id']
            target_ex['source_id'] = incoming_source
            target_ex['logical_sql'] = incoming_sql
            target_ex['assumptions'] = incoming_assumptions
            if expected is not None:
                target_ex['expected_rows'] = expected
            elif 'expected_rows' in target_ex:
                del target_ex['expected_rows']
        else:
            example_id = existing_staged['example_id'] if existing_staged else ('review-' + uuid.uuid4().hex)
            example = {
                'id': example_id,
                'source_id': incoming_source,
                'question': original['question'],
                'logical_sql': incoming_sql,
                'origin_review': original['id'],
                'assumptions': incoming_assumptions
            }
            if expected is not None:
                example['expected_rows'] = expected
            new_draft.setdefault('examples', []).append(example)

        record_id = existing_staged['id'] if existing_staged else uuid.uuid4().hex
        record = {
            'id': record_id,
            'kind': 'staged_example',
            'review_id': original['id'],
            'example_id': example_id,
            'question': original['question'],
            'at': time.time(),
            'state': '待复核草稿',
            'expected_fingerprint': fingerprint(expected) if expected is not None else None
        }

        # Step 1: Update draft in business
        original_draft = copy.deepcopy(draft)
        updated_cfg = business.update(actor, 'draft', {
            'business_project': bid,
            'expected_revision': cfg['revision'],
            'draft': new_draft
        })
        my_updated_revision = updated_cfg['revision']

        # Step 2: Append record to artifact with optimistic retry and CAS-safe rollback
        try:
            published_rec, _ = _publish_review_artifact(engine, scope, subject, record, replace_id=record_id)
        except Exception as pub_err:
            # CAS Rollback: only roll back if revision equals my_updated_revision and draft matches new_draft.
            # Never overwrite another administrator's concurrent changes!
            try:
                cur_cfg = business.config()
                if cur_cfg.get('revision') == my_updated_revision:
                    cur_proj = cur_cfg.get('projects', {}).get(bid)
                    if cur_proj and cur_proj.get('draft') == new_draft:
                        business.update(actor, 'draft', {
                            'business_project': bid,
                            'expected_revision': my_updated_revision,
                            'draft': original_draft
                        })
            except Exception:
                # Concurrent update occurred: preserve the other admin's state!
                pass
            raise ConflictError(f'草稿已更新，但复核工件写入未完成（{pub_err}）；保留当前草稿，支持重试幂等修复。') from pub_err

        return {'record': published_rec}

    else:
        raise ValueError('未知复核操作。')
