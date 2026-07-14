import config
from services import image_service
from services.image_service import ImageService, split_image_ref


def test_split_image_ref_with_tag():
    assert split_image_ref("ghcr.io/acemod/arma-reforger:latest") == (
        "ghcr.io/acemod/arma-reforger",
        "latest",
    )


def test_split_image_ref_defaults_tag_to_latest():
    assert split_image_ref("ghcr.io/acemod/arma-reforger") == (
        "ghcr.io/acemod/arma-reforger",
        "latest",
    )


def test_split_image_ref_keeps_registry_port():
    # The colon in a registry host:port must not be mistaken for a tag.
    assert split_image_ref("localhost:5000/img") == ("localhost:5000/img", "latest")
    assert split_image_ref("localhost:5000/img:v2") == ("localhost:5000/img", "v2")


def test_present_true_when_image_get_succeeds(monkeypatch):
    class FakeImages:
        def get(self, name):
            return object()

    class FakeClient:
        images = FakeImages()

    monkeypatch.setattr(image_service.docker_service, "get_client", lambda: FakeClient())
    assert ImageService().present() is True


def test_present_false_when_image_missing(monkeypatch):
    from docker.errors import ImageNotFound

    class FakeImages:
        def get(self, name):
            raise ImageNotFound("nope")

    class FakeClient:
        images = FakeImages()

    monkeypatch.setattr(image_service.docker_service, "get_client", lambda: FakeClient())
    assert ImageService().present() is False


def test_pull_aggregates_layers_and_reports_success(monkeypatch):
    """Drive _pull with a fake docker pull stream and check the job snapshot."""
    events = [
        {"status": "Pulling from acemod/arma-reforger", "id": "latest"},
        {"status": "Downloading", "id": "a", "progressDetail": {"current": 5, "total": 10}},
        {"status": "Downloading", "id": "b", "progressDetail": {"current": 0, "total": 10}},
        {"status": "Download complete", "id": "a"},
        {"status": "Download complete", "id": "b"},
        {"status": "Status: Downloaded newer image for x"},
    ]

    class FakeApi:
        def pull(self, repo, tag, stream, decode):
            assert stream and decode
            return iter(events)

    class FakeClient:
        api = FakeApi()

    monkeypatch.setattr(image_service.docker_service, "get_client", lambda: FakeClient())

    svc = ImageService()
    import time

    from services.image_service import PullJob

    job = PullJob(image=config.settings.reforger_server_image, started_at=time.time())

    # Run on a real loop so call_soon_threadsafe callbacks execute.
    import asyncio

    async def run():
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(svc._pull, job, loop)
        # let queued call_soon_threadsafe callbacks drain
        await asyncio.sleep(0)

    asyncio.run(run())
    # Both layers finished → 20/20 bytes accounted for.
    assert job.bytes_total == 20
    assert job.bytes_done == 20
    assert job.percent == 100.0
