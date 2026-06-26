from db import DB
from db.model.activity_excluded_channel import ActivityExcludedChannel
from db.model.activity_module_settings import ActivityModuleSettings


class ActivityModuleSettingsWrapper:
    def __init__(self, model: ActivityModuleSettings):
        self.model = model
        rows = DB.s.all(ActivityExcludedChannel, guild_id=model.guild_id)
        self._excluded_channels = {row.channel_id for row in rows}

    @property
    def excluded_channels(self):
        return self._excluded_channels

    def add_excluded_channel(self, channel_id: int):
        if channel_id in self._excluded_channels:
            return
        self._excluded_channels.add(channel_id)
        DB.s.add(ActivityExcludedChannel(guild_id=self.model.guild_id, channel_id=channel_id))
        DB.s.commit()

    def remove_excluded_channel(self, channel_id: int):
        self._excluded_channels.discard(channel_id)
        row = DB.s.first(ActivityExcludedChannel, guild_id=self.model.guild_id, channel_id=channel_id)
        if row is not None:
            DB.s.delete(row)
            DB.s.commit()
