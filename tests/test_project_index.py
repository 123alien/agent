from app.schemas.task import EvidenceChunk
from app.services.project_index import ProjectIndexService


def test_project_index_is_task_scoped_and_searchable(tmp_path, monkeypatch):
    service = ProjectIndexService()
    monkeypatch.setattr(type(service), "root", property(lambda self: tmp_path))
    chunks = [
        EvidenceChunk(
            chunk_id="F1-s1-c1-abcd", document_id="F1",
            content="项目名称：某市政务信息化平台升级项目。最高投标限价100万元。",
            section="项目基本信息", page=1, source_hash="abcd",
        ),
        EvidenceChunk(
            chunk_id="F1-s2-c1-efgh", document_id="F1",
            content="投标人应提交技术方案和服务响应方案。",
            section="资格要求", page=3, source_hash="efgh",
        ),
    ]
    metadata = service.build("T001", chunks)
    assert metadata["chunk_count"] == 2
    results = service.search("T001", "最高投标限价", 5)
    assert results
    assert results[0]["page"] == 1
    assert "100万元" in results[0]["content"]
    assert service.search("T001", "技术方案", document_id="F1")[0]["page"] == 3
    assert service.search("T001", "技术方案", document_id="missing") == []
    assert service.search("T001", "最高投标限价", page=3) == []
    assert service.search("T002", "最高投标限价") == []
