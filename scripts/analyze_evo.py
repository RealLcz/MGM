import json, glob, os, re, sys
from collections import Counter, defaultdict

BASES = ['output_mgm', 'output_hgm', 'output_mgm_wo_md']
ROOT = '/mnt/vast/home/ym56kacy/MendelGM'


def load(base):
    nodes = {}
    for f in glob.glob(os.path.join(ROOT, base, '*/metadata.json')):
        d = os.path.dirname(f)
        folder = os.path.basename(d)
        try:
            m = json.load(open(f))
        except Exception:
            continue
        m['_dir'] = d
        m['_folder'] = folder
        nodes[folder] = m
    return nodes


def acc(m):
    op = m.get('overall_performance') or {}
    return op.get('accuracy_score')


def rset(m):
    op = m.get('overall_performance') or {}
    return set(op.get('total_resolved_ids') or [])


def sset(m):
    op = m.get('overall_performance') or {}
    return set(op.get('total_submitted_ids') or [])


def motiv_entries(m):
    e = m.get('entries')
    if e:
        return [x for x in e if x and x != 'failed']
    if m.get('entry'):
        return [m['entry']]
    return []


def diff_stats(d):
    p = os.path.join(d, 'model_patch.diff')
    if not os.path.exists(p):
        return None
    txt = open(p, errors='ignore').read()
    files = re.findall(r'^\+\+\+ b/(.+)$', txt, re.M)
    added = len(re.findall(r'^\+(?!\+\+)', txt, re.M))
    removed = len(re.findall(r'^-(?!--)', txt, re.M))
    return {'bytes': len(txt), 'files': files, 'added': added, 'removed': removed,
            'empty': len(txt.strip()) == 0}


def main():
    for base in BASES:
        nodes = load(base)
        rows = []
        for folder, m in nodes.items():
            parent = m.get('parent_commit')
            strat = m.get('self_improve_strategy')
            if parent is None or strat is None:
                continue  # initial / root
            pm = nodes.get(parent)
            child_acc = acc(m)
            parent_acc = acc(pm) if pm else None
            ents = motiv_entries(m)
            cr, csb = rset(m), sset(m)
            pr, psb = (rset(pm), sset(pm)) if pm else (set(), set())
            # targeted: motivating entries the parent failed, did child resolve?
            tgt_parent_failed = [e for e in ents if (e in psb and e not in pr)]
            tgt_resolved_by_child = [e for e in tgt_parent_failed if e in cr]
            tgt_submitted_by_child = [e for e in tgt_parent_failed if e in csb]
            # shared-task comparison
            shared = csb & psb
            gained = [t for t in shared if t in cr and t not in pr]
            regressed = [t for t in shared if t in pr and t not in cr]
            ds = diff_stats(m['_dir'])
            rows.append({
                'folder': folder, 'strat': strat, 'parent': parent,
                'child_acc': child_acc, 'parent_acc': parent_acc,
                'n_entries': len(ents), 'tgt_parent_failed': len(tgt_parent_failed),
                'tgt_sub_child': len(tgt_submitted_by_child),
                'tgt_res_child': len(tgt_resolved_by_child),
                'shared': len(shared), 'gained': len(gained), 'regressed': len(regressed),
                'diff': ds,
            })
        # aggregate
        print('=' * 70)
        print(f'### {base}: {len(rows)} improved (non-root) nodes')
        # strategy
        sc = Counter(r['strat'] for r in rows)
        print('strategy:', dict(sc))
        # diagnose efficacy
        with_acc = [r for r in rows if r['child_acc'] is not None and r['parent_acc'] is not None]
        dacc = [r['child_acc'] - r['parent_acc'] for r in with_acc]
        improved = sum(1 for x in dacc if x > 1e-9)
        worse = sum(1 for x in dacc if x < -1e-9)
        same = len(dacc) - improved - worse
        print(f'\n(1) DIAGNOSE EFFICACY')
        print(f'  child_acc vs parent_acc (n={len(dacc)}): improved={improved} same={same} worse={worse}')
        if dacc:
            print(f'  mean Δacc={sum(dacc)/len(dacc):+.3f}  max={max(dacc):+.3f} min={min(dacc):+.3f}')
        # targeted fix
        tgt_total = sum(r['tgt_parent_failed'] for r in rows)
        tgt_sub = sum(r['tgt_sub_child'] for r in rows)
        tgt_res = sum(r['tgt_res_child'] for r in rows)
        print(f'  targeted failed-entries (parent failed them): {tgt_total}')
        print(f'    of those, re-submitted by child: {tgt_sub};  resolved by child: {tgt_res}')
        if tgt_sub:
            print(f'    targeted fix rate (resolved/re-submitted) = {tgt_res/tgt_sub:.1%}')
        # shared task
        sh = sum(r['shared'] for r in rows)
        ga = sum(r['gained'] for r in rows)
        re_ = sum(r['regressed'] for r in rows)
        print(f'  shared tasks (child∩parent submitted): {sh}; net gained={ga} regressed={re_} net={ga-re_:+d}')
        # implementation fidelity
        print(f'\n(2) IMPLEMENTATION FIDELITY')
        ds = [r['diff'] for r in rows if r['diff']]
        empty = sum(1 for d in ds if d['empty'])
        print(f'  patches: {len(ds)} total, empty={empty}')
        all_files = Counter()
        for d in ds:
            for fp in d['files']:
                all_files[fp] += 1
        print('  most-touched files:')
        for fp, c in all_files.most_common(12):
            print(f'    {c:3d}  {fp}')
        # touches expected agent core?
        core = re.compile(r'coding_agent.*\.py|^tools/|^prompts/|^utils/')
        touches_core = sum(1 for d in ds if any(core.search(fp) for fp in d['files']))
        print(f'  patches touching agent core (coding_agent/tools/prompts/utils): {touches_core}/{len(ds)}')
        avgf = sum(len(d['files']) for d in ds) / max(1, len(ds))
        avga = sum(d['added'] for d in ds) / max(1, len(ds))
        print(f'  avg files/patch={avgf:.1f}  avg +lines/patch={avga:.0f}')
        print()


if __name__ == '__main__':
    main()
