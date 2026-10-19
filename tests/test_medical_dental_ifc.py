import json
from pathlib import Path
from src.medical_dental_ifc import EXPECTED

ROOT=Path(__file__).resolve().parents[1]
def test_official_expected_hashes_are_complete_and_well_formed():
    assert len(EXPECTED)==5
    assert all(len(value)==64 and int(value,16)>=0 for value in EXPECTED.values())
def test_published_progressive_manifest_preserves_source_identity_and_declares_no_alignment():
    data=json.loads((ROOT/'viewer/medical_dental_clinic_progressive.json').read_text())
    assert data['kind']=='REAL_IFC_PROGRESSIVE_MANIFEST'
    assert data['applied_alignment_transform'] is None
    assert data['routing_benchmark_authorized'] is False
    assert {entry['file_name']:entry['sha256'] for entry in data['source_manifest']['files'].values()}==EXPECTED
    assert all(layer['chunks'] for layer in data['layers'])
