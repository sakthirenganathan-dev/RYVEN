"""TimeTool returning current local time and date."""

from datetime import datetime
from typing import Any, Dict
from app.tools.base import BaseTool


class TimeTool(BaseTool):
    """Tool that inspects local system clock and returns formatted time and date."""

    name = "time"
    description = "Provides current local time, date, day of week, and timezone."
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Fetch current local system time and date."""
        now = datetime.now()
        local_time_12h = now.strftime("%I:%M %p").lstrip("0")
        local_time_24h = now.strftime("%H:%M:%S")
        formatted_date = now.strftime("%A, %B %d, %Y")
        iso_timestamp = now.isoformat()

        human_message = f"It is {local_time_12h} on {formatted_date}."

        return {
            "time": local_time_12h,
            "time_24h": local_time_24h,
            "date": formatted_date,
            "day_of_week": now.strftime("%A"),
            "iso": iso_timestamp,
            "message": human_message,
        }
