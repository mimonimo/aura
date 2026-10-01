import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('tree_builder', Path(__file__).parents[1]/'scripts/153_build_tree_cot.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def args():
    return ['--form','1','--done','2','--notice','3','--basic','4','--manual','5',
            '--program-id','program:example','--program','합성 사업']


def test_explicit_document_set_and_stable_program_id():
    parsed = builder.parse_args(args())
    assert parsed.program_id == 'program:example'
    assert parsed.program_year is None
    assert not parsed.push


@pytest.mark.parametrize('argv', [[], args()+['--replace'], args()+['--form','0'],
                                  args()+['--program-id','docset:1:2'], args()+['--program',' ']])
def test_no_hardcoded_document_defaults_or_destructive_replacement(argv):
    with pytest.raises(SystemExit):
        builder.parse_args(argv)
