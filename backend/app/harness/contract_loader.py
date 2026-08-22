"""Writing Contract 다섯 파일을 읽고 검사한다 (README §2.15).

manifest 1개(`contract.yaml`)와 설정 4개(`profile/template/style/validation`)가
모두 있고 ID·버전·필수 필드가 맞을 때만 그 버전이 런타임 정본이 된다.
하나라도 없거나 어긋나면 서버를 시작하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "writing_contracts" / "assembly_law_amendment_v1"

#: manifest가 가리켜야 하는 설정 파일 이름.
COMPONENT_FILES = {
    "profile": "profile.yaml",
    "template": "template.yaml",
    "style": "style.yaml",
    "validation": "validation.yaml",
}

#: 각 설정 파일이 반드시 가져야 하는 필드.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "profile": ("profile_id", "version", "institution", "legislative_terms"),
    "template": ("template_id", "version", "sections", "required_sections"),
    "style": ("style_id", "version", "warning_thresholds"),
    "validation": ("validation_id", "version", "blocking_checks", "forbidden_phrases"),
}

#: 설정 파일 안에서 자기 ID를 담는 필드 이름.
ID_FIELDS = {
    "profile": "profile_id",
    "template": "template_id",
    "style": "style_id",
    "validation": "validation_id",
}


class ContractLoadError(RuntimeError):
    """Writing Contract를 읽거나 검사하지 못했을 때."""


@dataclass(frozen=True)
class WritingContract:
    """한 실행에 적용할 작성 기준 묶음 (§2.10의 WritingContract)."""

    contract_id: str
    version: int | str
    manifest: dict[str, Any]
    profile: dict[str, Any]
    template: dict[str, Any]
    style: dict[str, Any]
    validation: dict[str, Any]
    source_paths: dict[str, str]

    @property
    def full_id(self) -> str:
        """`id@version` 형태의 전체 ID."""
        return f"{self.contract_id}@{self.version}"

    def component_full_id(self, name: str) -> str:
        component: dict[str, Any] = getattr(self, name)
        return f"{component[ID_FIELDS[name]]}@{component['version']}"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractLoadError(f"Writing Contract 파일이 없습니다: {path.name}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractLoadError(f"{path.name}을 읽지 못했습니다: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ContractLoadError(f"{path.name}의 최상위는 사전(mapping)이어야 합니다.")
    return loaded


def load_writing_contract(directory: Path | None = None) -> WritingContract:
    """다섯 파일을 읽고 서로 맞는지 확인한 뒤 돌려준다."""
    base = directory or CONTRACT_DIR
    manifest = _read_yaml(base / "contract.yaml")

    for field in ("contract_id", "version", "components"):
        if field not in manifest:
            raise ContractLoadError(f"contract.yaml에 `{field}`가 없습니다.")

    components: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {"contract": str((base / "contract.yaml").name)}

    for name, filename in COMPONENT_FILES.items():
        data = _read_yaml(base / filename)
        for field in REQUIRED_FIELDS[name]:
            if field not in data:
                raise ContractLoadError(f"{filename}에 `{field}`가 없습니다.")
        components[name] = data
        paths[name] = filename

    # manifest가 적어 둔 `id@version`과 실제 파일 내용이 같아야 한다.
    declared: dict[str, str] = manifest["components"]
    for name in COMPONENT_FILES:
        if name not in declared:
            raise ContractLoadError(f"contract.yaml의 components에 `{name}`이 없습니다.")
        data = components[name]
        actual = f"{data[ID_FIELDS[name]]}@{data['version']}"
        if declared[name] != actual:
            raise ContractLoadError(
                f"`{name}` 버전이 manifest와 다릅니다. "
                f"manifest={declared[name]} 실제={actual}"
            )

    return WritingContract(
        contract_id=manifest["contract_id"],
        version=manifest["version"],
        manifest=manifest,
        profile=components["profile"],
        template=components["template"],
        style=components["style"],
        validation=components["validation"],
        source_paths=paths,
    )
