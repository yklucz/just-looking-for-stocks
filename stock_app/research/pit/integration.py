"""Frozen PIT provenance for future consumers; never upgrades a legacy experiment."""
from copy import deepcopy
from pathlib import Path
from ..integrity import file_status
from .query import leakage_check
from .store import get


def with_pit_inputs(path,contract,manifest):
    review=leakage_check(path,manifest)
    if review['status'] in {'invalid','unverifiable'}: raise ValueError('Invalid PIT input provenance')
    updated=deepcopy(contract)
    updated['data']['pit_manifest']=deepcopy(manifest)
    return updated


def evidence_checks(path,manifest,*,depth='metadata'):
    review=leakage_check(path,manifest)
    result=[{'reference':'pit:'+str(i),'status':{'safe':'verified','warning':'unverifiable','invalid':'mismatch','unverifiable':'unverifiable'}[v['status']],
             'reason':v['reason'],'pit_status':v['status']} for i,v in enumerate(review['checks'])]
    if depth=='artifact':
        for entry in manifest.get('entries',[]):
            try:
                raw=get(path,'raw',entry['raw_id'])
                status,reason=file_status(raw['document'],deep=True)
            except KeyError:
                status,reason='missing','PIT raw record absent'
            result.append({'reference':'pit.raw:'+entry.get('raw_id','unknown'),'status':status,'reason':reason})
    return result


def operational_handler(pit,parser):
    """Adapter for an explicitly registered future Phase 0 job; schedules nothing.

    Parser receives (pit, pinned_inputs), must use content/observation identities
    for idempotence, and must not fetch providers. Preparation/fetch belongs before
    the existing effect fence. Partial failures remain Phase 0 blocked failures.
    """
    def execute(job,context):
        from ...jobs.core import UnsafeExecution
        pinned=context.store.get(context.run_id).get('inputs',{})
        if not pinned.get('raw_id'): raise ValueError('Pinned raw evidence required')
        raw=get(pit.store.path,'raw',pinned['raw_id'])
        status,_=file_status(raw['document'],deep=True)
        if status!='verified': raise ValueError('Pinned raw evidence unavailable')
        context.begin_effects({'raw_id':raw['id'],'raw_sha256':raw['sha256']})
        try:
            revisions=parser(pit,pinned)
            return {'pit_revision_ids':[r['id'] for r in revisions],'raw_id':raw['id']}
        except Exception as exc:
            raise UnsafeExecution('PIT normalization may have committed; inspect immutable evidence') from exc
    return execute
