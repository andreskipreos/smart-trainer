import asyncio

from smart_trainer import ftms
from smart_trainer.devices import FtmsTrainer


class FakeClient:
    """Mimics a bleak client: answers control point writes with FTMS indications."""

    def __init__(self, trainer, deny_first=False):
        self.trainer = trainer
        self.writes = []
        self.deny_first = deny_first
        self.is_connected = True

    async def write_gatt_char(self, uuid, data, response=True):
        assert uuid == ftms.CONTROL_POINT
        self.writes.append(bytes(data))
        result = ftms.ResultCode.SUCCESS
        if self.deny_first and data[0] != ftms.OpCode.REQUEST_CONTROL:
            self.deny_first = False
            result = ftms.ResultCode.CONTROL_NOT_PERMITTED
        loop = asyncio.get_running_loop()
        loop.call_soon(self.trainer._on_control_point, None,
                       bytearray([0x80, data[0], result]))


def test_erg_and_sim_commands_are_sent():
    async def run():
        tr = FtmsTrainer(address="AA")
        tr._client = FakeClient(tr)
        tr.connected = True
        tr._start_worker()
        tr.set_erg(250)
        await asyncio.sleep(0.05)
        tr.set_sim(4.5)
        await asyncio.sleep(0.05)
        await tr.disconnect()
        return tr._client.writes

    writes = asyncio.run(run())
    assert writes[0] == ftms.encode_set_target_power(250)
    assert writes[1] == ftms.encode_indoor_bike_simulation(4.5)


def test_rerequests_control_when_not_permitted():
    async def run():
        tr = FtmsTrainer(address="AA")
        tr._client = FakeClient(tr, deny_first=True)
        tr.connected = True
        tr._start_worker()
        tr.set_erg(180)
        await asyncio.sleep(0.05)
        await tr.disconnect()
        return tr._client.writes

    writes = asyncio.run(run())
    assert writes[:3] == [ftms.encode_set_target_power(180), ftms.encode_request_control(),
                          ftms.encode_set_target_power(180)]


def test_bike_data_notifications_merge():
    tr = FtmsTrainer(address="AA")
    import struct
    tr._on_bike_data(None, bytearray(struct.pack("<HHHh", (1 << 2) | (1 << 6), 3000, 170, 210)))
    tr._on_bike_data(None, bytearray(struct.pack("<HB", 1 | (1 << 9), 150)))
    assert tr.data.power_w == 210 and tr.data.cadence_rpm == 85 and tr.data.heart_rate == 150
