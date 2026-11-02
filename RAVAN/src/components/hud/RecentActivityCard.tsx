import { useState, useEffect } from "react";
import {
  getActivityLog,
  formatTimeAgo,
  type ActivityItem,
} from "@/services/activity";

export function RecentActivityCard() {
  const [activities, setActivities] = useState<ActivityItem[]>([]);

  useEffect(() => {
    const refresh = () => {
      setActivities(getActivityLog());
    };
    refresh();

    window.addEventListener("ryven_activity_updated", refresh);
    const interval = setInterval(refresh, 10000); // refresh relative timestamps every 10s

    return () => {
      window.removeEventListener("ryven_activity_updated", refresh);
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="glass-panel p-3.5">
      {/* Header */}
      <div className="flex items-center justify-between pb-2">
        <span className="section-title">
          RECENT ACTIVITY
        </span>
        <span className="text-[9px] font-mono text-slate-500">SESSION</span>
      </div>

      {/* List */}
      <div className="space-y-2 text-[11px]">
        {activities.length === 0 ? (
          <div className="py-2 text-center text-[10px] text-slate-500 font-mono">
            No events recorded in this session yet.
          </div>
        ) : (
          activities.slice(0, 5).map((item) => (
            <div key={item.id} className="flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.dotColor}`} />
                <span className="truncate text-slate-300 font-normal">{item.text}</span>
              </div>
              <span className="ml-2 shrink-0 font-mono text-[10px] text-slate-500">
                {formatTimeAgo(item.timestamp)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
