import { useEffect, useState, useCallback } from "react";
import {
  Brain,
  Search,
  Trash2,
  Download,
  ShieldCheck,
  ShieldAlert,
  Lock,
  RefreshCw,
  Sliders,
  Tag,
  Folder,
  Calendar,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Plus,
  Eye,
  Info,
  Key,
  FileText,
  Copy,
  Check,
  Database,
  Layers,
} from "lucide-react";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import {
  searchMemories,
  getMemory,
  getPreferences,
  savePreference,
  deletePreference,
  deleteMemory,
  deleteTaskMemories,
  deleteProjectMemories,
  exportMemories,
  getMemoryStats,
  type MemoryItem,
  type UserPreference,
  type MemoryStats,
  type MemoryExportResult,
} from "@/services/agents";

const TRUST_BADGE_STYLE: Record<string, { label: string; className: string }> = {
  USER_CONFIRMED: {
    label: "USER CONFIRMED",
    className: "text-signal border-signal/40 bg-signal/10",
  },
  SYSTEM_DERIVED: {
    label: "SYSTEM DERIVED",
    className: "text-holo border-holo/40 bg-holo/10",
  },
  TASK_DERIVED: {
    label: "TASK DERIVED",
    className: "text-violet border-violet/40 bg-violet/10",
  },
  DOCUMENT_DERIVED: {
    label: "DOCUMENT DERIVED",
    className: "text-blue-400 border-blue-400/40 bg-blue-400/10",
  },
  WEB_DERIVED: {
    label: "WEB DERIVED",
    className: "text-amber-400 border-amber-400/40 bg-amber-400/10",
  },
};

type MemoryHUDTab = "overview" | "memories" | "preferences" | "purge" | "export";

export interface MemoryHUDProps {
  standalone?: boolean;
}

export function MemoryHUD({ standalone = false }: MemoryHUDProps) {
  // Navigation
  const [activeTab, setActiveTab] = useState<MemoryHUDTab>("overview");

  // Telemetry & Statistics
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [statsLoading, setStatsLoading] = useState<boolean>(true);

  // Search & Inspection State
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [searchType, setSearchType] = useState<string>("ALL");
  const [searchProject, setSearchProject] = useState<string>("");
  const [searchResults, setSearchResults] = useState<MemoryItem[]>([]);
  const [searching, setSearching] = useState<boolean>(false);
  const [hasSearched, setHasSearched] = useState<boolean>(false);
  const [selectedMemory, setSelectedMemory] = useState<MemoryItem | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);

  // Preferences State
  const [preferences, setPreferences] = useState<UserPreference[]>([]);
  const [prefsLoading, setPrefsLoading] = useState<boolean>(false);
  const [newPrefKey, setNewPrefKey] = useState<string>("");
  const [newPrefValue, setNewPrefValue] = useState<string>("");
  const [newPrefProject, setNewPrefProject] = useState<string>("");
  const [savingPref, setSavingPref] = useState<boolean>(false);

  // Export State
  const [exportData, setExportData] = useState<MemoryExportResult | null>(null);
  const [exporting, setExporting] = useState<boolean>(false);

  // Purge / Destructive State
  const [purgeTaskId, setPurgeTaskId] = useState<string>("");
  const [purgeProjectId, setPurgeProjectId] = useState<string>("");
  const [purgeConfirmationToken, setPurgeConfirmationToken] = useState<string>("");
  const [showProjectConfirmModal, setShowProjectConfirmModal] = useState<boolean>(false);
  const [purging, setPurging] = useState<boolean>(false);

  // Global Notification / Alert
  const [alert, setAlert] = useState<{ text: string; isError: boolean } | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Fetch Stats
  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const data = await getMemoryStats();
      setStats(data);
    } catch (err) {
      console.warn("Memory stats load error:", err);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  // Fetch Preferences
  const loadPreferences = useCallback(async (proj?: string) => {
    setPrefsLoading(true);
    try {
      const data = await getPreferences(proj || undefined);
      setPreferences(data.preferences || []);
    } catch (err) {
      console.warn("Preferences load error:", err);
      setAlert({ text: `Failed to load preferences: ${String(err)}`, isError: true });
    } finally {
      setPrefsLoading(false);
    }
  }, []);

  // Initial Load
  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Load preferences when tab opened
  useEffect(() => {
    if (activeTab === "preferences") {
      loadPreferences();
    }
  }, [activeTab, loadPreferences]);

  // Execute Search
  const handleSearch = async () => {
    if (!searchQuery.trim() && searchType === "ALL" && !searchProject.trim()) {
      setSearchResults([]);
      setHasSearched(false);
      return;
    }
    setSearching(true);
    setAlert(null);
    try {
      const res = await searchMemories(
        searchQuery.trim(),
        searchProject.trim() || undefined,
        searchType,
        5
      );
      setSearchResults(res.results);
      setHasSearched(true);
      if (res.results.length > 0 && !selectedMemory) {
        setSelectedMemory(res.results[0]);
      }
    } catch (err) {
      setAlert({ text: `Search failed: ${String(err)}`, isError: true });
    } finally {
      setSearching(false);
    }
  };

  // Inspect Memory Detail
  const handleSelectMemory = async (item: MemoryItem) => {
    setSelectedMemory(item);
    setLoadingDetail(true);
    try {
      const fresh = await getMemory(item.memory_id);
      setSelectedMemory(fresh);
    } catch {
      // Use existing item if individual fetch fails
    } finally {
      setLoadingDetail(false);
    }
  };

  // Delete Individual Memory
  const handleDeleteMemory = async (memoryId: string) => {
    if (!window.confirm(`Permanently delete memory ${memoryId.slice(0, 16)}...?`)) return;
    try {
      await deleteMemory(memoryId);
      setAlert({ text: `Memory ${memoryId.slice(0, 12)}... deleted successfully.`, isError: false });
      setSelectedMemory(null);
      setSearchResults((prev) => prev.filter((m) => m.memory_id !== memoryId));
      loadStats();
    } catch (err) {
      setAlert({ text: `Failed to delete memory: ${String(err)}`, isError: true });
    }
  };

  // Save Preference
  const handleSavePreference = async () => {
    if (!newPrefKey.trim() || !newPrefValue.trim() || savingPref) return;
    setSavingPref(true);
    setAlert(null);
    try {
      await savePreference(
        newPrefKey.trim(),
        newPrefValue.trim(),
        newPrefProject.trim() || undefined
      );
      setAlert({ text: `Preference '${newPrefKey}' saved.`, isError: false });
      setNewPrefKey("");
      setNewPrefValue("");
      setNewPrefProject("");
      loadPreferences();
      loadStats();
    } catch (err) {
      setAlert({ text: `Preference save rejected: ${String(err)}`, isError: true });
    } finally {
      setSavingPref(false);
    }
  };

  // Delete Preference
  const handleDeletePreference = async (key: string, projId?: string | null) => {
    if (!window.confirm(`Delete preference '${key}'?`)) return;
    try {
      await deletePreference(key, projId || undefined);
      setAlert({ text: `Preference '${key}' forgotten.`, isError: false });
      loadPreferences();
      loadStats();
    } catch (err) {
      setAlert({ text: `Failed to forget preference: ${String(err)}`, isError: true });
    }
  };

  // Delete Task Memories
  const handleDeleteTaskMemories = async () => {
    if (!purgeTaskId.trim() || purging) return;
    setPurging(true);
    setAlert(null);
    try {
      const res = await deleteTaskMemories(purgeTaskId.trim());
      setAlert({
        text: `Removed ${res.count} episodic memories for task ${purgeTaskId}. Authoritative execution history preserved.`,
        isError: false,
      });
      setPurgeTaskId("");
      loadStats();
    } catch (err) {
      setAlert({ text: `Task memory delete failed: ${String(err)}`, isError: true });
    } finally {
      setPurging(false);
    }
  };

  // Delete Project Memories (Confirmation workflow)
  const handleDeleteProjectMemories = async () => {
    if (!purgeProjectId.trim() || purging) return;
    setPurging(true);
    setAlert(null);
    try {
      const res = await deleteProjectMemories(
        purgeProjectId.trim(),
        purgeConfirmationToken.trim() || undefined
      );
      setAlert({
        text: `Project '${purgeProjectId}' purged: ${res.count} memories deleted.`,
        isError: false,
      });
      setShowProjectConfirmModal(false);
      setPurgeProjectId("");
      setPurgeConfirmationToken("");
      loadStats();
    } catch (err: unknown) {
      const errWithStatus = err as { status?: number; message?: string };
      if (errWithStatus.status === 409) {
        setAlert({
          text: `Confirmation required (409): Destructive project memory deletion requires a valid, user-confirmed confirmation token.`,
          isError: true,
        });
      } else {
        setAlert({ text: `Project purge failed: ${String(err)}`, isError: true });
      }
    } finally {
      setPurging(false);
    }
  };

  // Export Memories
  const handleExport = async () => {
    setExporting(true);
    setAlert(null);
    try {
      const res = await exportMemories(undefined, 100);
      setExportData(res);
      setAlert({
        text: `Export ready: ${res.total_exported} sanitized memories generated.`,
        isError: false,
      });
    } catch (err) {
      setAlert({ text: `Export failed: ${String(err)}`, isError: true });
    } finally {
      setExporting(false);
    }
  };

  // Download Export JSON
  const handleDownloadJSON = () => {
    if (!exportData) return;
    const blob = new Blob([JSON.stringify(exportData, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ryven_memories_export_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Copy ID Helper
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(text);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Content body
  const content = (
    <div className="space-y-3.5 text-xs">
      {/* Alert Banner */}
      {alert && (
        <div
          className={`flex items-start justify-between gap-2 p-2 rounded-sm border ${
            alert.isError
              ? "bg-red-500/10 border-red-500/40 text-red-200"
              : "bg-signal/10 border-signal/40 text-signal"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <div className="flex items-center gap-1.5 text-[10px]">
            {alert.isError ? (
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-400" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-signal" />
            )}
            <span>{alert.text}</span>
          </div>
          <button
            type="button"
            onClick={() => setAlert(null)}
            className="text-muted-foreground hover:text-foreground text-[10px]"
          >
            <XCircle className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Navigation Sub-Tabs */}
      <div className="flex gap-1 border-b border-border/40 pb-2 overflow-x-auto">
        <button
          type="button"
          onClick={() => setActiveTab("overview")}
          className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest flex items-center gap-1 ${
            activeTab === "overview"
              ? "bg-holo/20 text-holo border border-holo/40"
              : "text-muted-foreground hover:text-foreground"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <Database className="h-2.5 w-2.5" />
          OVERVIEW
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("memories")}
          className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest flex items-center gap-1 ${
            activeTab === "memories"
              ? "bg-holo/20 text-holo border border-holo/40"
              : "text-muted-foreground hover:text-foreground"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <Brain className="h-2.5 w-2.5" />
          MEMORIES
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("preferences")}
          className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest flex items-center gap-1 ${
            activeTab === "preferences"
              ? "bg-holo/20 text-holo border border-holo/40"
              : "text-muted-foreground hover:text-foreground"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <Sliders className="h-2.5 w-2.5" />
          PREFERENCES
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("purge")}
          className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest flex items-center gap-1 ${
            activeTab === "purge"
              ? "bg-holo/20 text-holo border border-holo/40"
              : "text-muted-foreground hover:text-foreground"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <Trash2 className="h-2.5 w-2.5" />
          PURGE
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("export")}
          className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest flex items-center gap-1 ${
            activeTab === "export"
              ? "bg-holo/20 text-holo border border-holo/40"
              : "text-muted-foreground hover:text-foreground"
          }`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <Download className="h-2.5 w-2.5" />
          EXPORT
        </button>
      </div>

      {/* ============================================================ */}
      {/* 1. OVERVIEW TAB                                              */}
      {/* ============================================================ */}
      {activeTab === "overview" && (
        <div className="space-y-3">
          <div className="holo-corners border border-border/70 p-2.5 bg-background/60">
            <div className="flex items-center justify-between pb-2 border-b border-border/40">
              <div className="flex items-center gap-2">
                <span
                  className="text-[11px] font-semibold tracking-wider uppercase text-foreground"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  Memory Plane
                </span>
                <StatusIndicator
                  label={statsLoading ? "SYNCING" : "ONLINE"}
                  tone={statsLoading ? "violet" : "signal"}
                  pulse={statsLoading}
                />
              </div>
              <button
                type="button"
                onClick={loadStats}
                disabled={statsLoading}
                className="text-[10px] text-muted-foreground hover:text-holo flex items-center gap-1 transition-colors"
                title="Refresh memory statistics"
              >
                <RefreshCw className={`h-3 w-3 ${statsLoading ? "animate-spin" : ""}`} />
              </button>
            </div>

            <div className="grid grid-cols-3 gap-1.5 pt-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">TOTAL</span>
                <span className="text-holo font-bold text-sm">
                  {stats?.total_memories ?? 0}
                </span>
              </div>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">SEMANTIC</span>
                <span className="text-foreground font-bold text-sm">
                  {stats?.semantic_count ?? 0}
                </span>
              </div>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">EPISODIC</span>
                <span className="text-violet font-bold text-sm">
                  {stats?.episodic_count ?? 0}
                </span>
              </div>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">PREFERENCES</span>
                <span className="text-signal font-bold text-sm">
                  {stats?.preference_count ?? 0}
                </span>
              </div>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">PROJECT SCOPE</span>
                <span className="text-foreground font-bold text-sm">
                  {stats?.project_scoped_count ?? 0}
                </span>
              </div>
              <div className="bg-holo/5 border border-border/50 p-2 rounded-sm">
                <span className="text-muted-foreground block text-[9px]">GLOBAL</span>
                <span className="text-holo font-bold text-sm">
                  {stats?.global_count ?? 0}
                </span>
              </div>
            </div>
          </div>

          {/* Quick Search Launcher */}
          <div className="border border-border/60 p-2.5 bg-background/50 space-y-2">
            <span
              className="text-[10px] text-muted-foreground uppercase tracking-widest block"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              Quick Search Context
            </span>
            <div className="flex gap-1.5">
              <input
                type="text"
                placeholder="Search semantic & episodic memories..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    setActiveTab("memories");
                    handleSearch();
                  }
                }}
                className="flex-1 bg-background border border-border/60 px-2.5 py-1 text-[11px] rounded-sm focus:outline-none focus:border-holo"
                style={{ fontFamily: "var(--font-mono)" }}
              />
              <button
                type="button"
                onClick={() => {
                  setActiveTab("memories");
                  handleSearch();
                }}
                className="px-3 py-1 bg-holo/20 text-holo border border-holo/40 hover:bg-holo/30 rounded-sm text-[10px] font-mono flex items-center gap-1 transition-colors"
              >
                <Search className="h-3 w-3" />
                SEARCH
              </button>
            </div>
          </div>

          {/* Policy Notice Box */}
          <div className="border border-border/40 p-2 bg-background/40 flex items-start gap-2">
            <ShieldCheck className="h-4 w-4 text-holo shrink-0 mt-0.5" />
            <div className="text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              <span className="text-foreground font-semibold block">DURABLE MEMORY ENGINE</span>
              Advisory context service for user preferences, completed tasks, and semantic knowledge. Memory is never execution authority or safety bypass.
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* 2. MEMORIES SEARCH & INSPECTION TAB                          */}
      {/* ============================================================ */}
      {activeTab === "memories" && (
        <div className="space-y-3">
          {/* Search Controls */}
          <div className="border border-border/70 p-2 bg-background/60 space-y-2">
            <div className="flex gap-1.5">
              <input
                type="text"
                placeholder="Query memory content..."
                maxLength={256}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                className="flex-1 bg-background border border-border/60 px-2 py-1 text-[11px] rounded-sm focus:outline-none focus:border-holo"
                style={{ fontFamily: "var(--font-mono)" }}
              />
              <button
                type="button"
                onClick={handleSearch}
                disabled={searching}
                className="px-2.5 py-1 bg-holo/20 text-holo border border-holo/40 hover:bg-holo/30 rounded-sm text-[10px] font-mono flex items-center gap-1 transition-colors"
              >
                <Search className={`h-3 w-3 ${searching ? "animate-spin" : ""}`} />
                FIND
              </button>
              {hasSearched && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery("");
                    setSearchResults([]);
                    setSelectedMemory(null);
                    setHasSearched(false);
                  }}
                  className="px-2 py-1 text-muted-foreground hover:text-foreground text-[10px] font-mono border border-border/40 rounded-sm"
                >
                  CLEAR
                </button>
              )}
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border/40 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
              <div className="flex items-center gap-1">
                <span className="text-muted-foreground text-[9px]">TYPE:</span>
                <select
                  value={searchType}
                  onChange={(e) => setSearchType(e.target.value)}
                  className="bg-background border border-border/60 px-1.5 py-0.5 text-[9px] rounded-sm text-foreground focus:outline-none"
                >
                  <option value="ALL">ALL TYPES</option>
                  <option value="SEMANTIC">SEMANTIC</option>
                  <option value="EPISODIC">EPISODIC</option>
                  <option value="PREFERENCE">PREFERENCE</option>
                </select>
              </div>

              <div className="flex items-center gap-1">
                <span className="text-muted-foreground text-[9px]">PROJECT:</span>
                <input
                  type="text"
                  placeholder="Optional scope..."
                  value={searchProject}
                  onChange={(e) => setSearchProject(e.target.value)}
                  className="bg-background border border-border/60 px-1.5 py-0.5 text-[9px] rounded-sm w-24 text-foreground focus:outline-none"
                />
              </div>

              <span className="text-muted-foreground text-[9px] ml-auto">
                LIMIT: 5 MAX
              </span>
            </div>
          </div>

          {/* Results + Details Two-Section Layout */}
          <div className="space-y-3">
            {/* Search Results List */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
                <span>RESULTS ({searchResults.length})</span>
                {hasSearched && <span>Bounded context retrieval</span>}
              </div>

              {searchResults.length === 0 ? (
                <div className="border border-dashed border-border/60 p-4 text-center space-y-1">
                  <p className="text-muted-foreground text-[11px]" style={{ fontFamily: "var(--font-mono)" }}>
                    {hasSearched
                      ? "No memories found. Try a different query or loosen project filters."
                      : "Enter a search term above to inspect relevant memory."}
                  </p>
                </div>
              ) : (
                <div className="space-y-1.5 max-h-48 overflow-y-auto pr-0.5">
                  {searchResults.map((item) => {
                    const isSelected = selectedMemory?.memory_id === item.memory_id;
                    const badge = TRUST_BADGE_STYLE[item.trust_level] || {
                      label: item.trust_level,
                      className: "text-muted-foreground border-border/40",
                    };

                    return (
                      <div
                        key={item.memory_id}
                        onClick={() => handleSelectMemory(item)}
                        className={`p-2 border rounded-sm cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-holo/10 border-holo text-foreground"
                            : "bg-background/70 border-border/60 hover:border-border text-muted-foreground"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-1 mb-1">
                          <div className="flex items-center gap-1">
                            <span
                              className="text-[9px] px-1 py-0.2 rounded font-mono border bg-background/80"
                            >
                              {item.memory_type}
                            </span>
                            <span
                              className={`text-[8px] px-1 py-0.2 rounded font-mono border ${badge.className}`}
                            >
                              {badge.label}
                            </span>
                          </div>
                          <span className="text-[9px] font-mono text-muted-foreground">
                            {item.project_id || "GLOBAL"}
                          </span>
                        </div>

                        <p className="text-[11px] line-clamp-2 text-foreground font-mono">
                          {item.content}
                        </p>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Selected Memory Detail View */}
            {selectedMemory && (
              <div className="holo-corners border border-border/80 p-2.5 bg-background/80 space-y-2">
                <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
                  <div className="flex items-center gap-1.5">
                    <Eye className="h-3.5 w-3.5 text-holo" />
                    <span
                      className="text-[11px] font-semibold text-foreground uppercase tracking-wider"
                      style={{ fontFamily: "var(--font-display)" }}
                    >
                      Memory Detail
                    </span>
                  </div>
                  <div className="flex items-center gap-1 text-[9px] font-mono text-muted-foreground">
                    <span>{selectedMemory.memory_id.slice(0, 16)}...</span>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(selectedMemory.memory_id)}
                      className="hover:text-holo transition-colors"
                      title="Copy Memory ID"
                    >
                      {copiedId === selectedMemory.memory_id ? (
                        <Check className="h-2.5 w-2.5 text-signal" />
                      ) : (
                        <Copy className="h-2.5 w-2.5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Metadata Grid */}
                <div className="grid grid-cols-2 gap-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
                  <div>
                    <span className="text-muted-foreground text-[9px] block">TYPE / SOURCE</span>
                    <span className="text-foreground font-semibold">
                      {selectedMemory.memory_type} · {selectedMemory.source}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-[9px] block">TRUST / TAINT</span>
                    <div className="flex items-center gap-1">
                      <span className="text-holo font-semibold">
                        {selectedMemory.trust_level}
                      </span>
                      <span className="text-muted-foreground">·</span>
                      <span className={selectedMemory.taint_status === "CLEAN" ? "text-signal" : "text-amber-400"}>
                        {selectedMemory.taint_status || "CLEAN"}
                      </span>
                    </div>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-[9px] block">PROJECT / TASK</span>
                    <span className="text-foreground">
                      {selectedMemory.project_id || "GLOBAL"} {selectedMemory.task_id ? `(${selectedMemory.task_id.slice(0, 10)}...)` : ""}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-[9px] block">CREATED</span>
                    <span className="text-muted-foreground">
                      {selectedMemory.created_at ? new Date(selectedMemory.created_at).toLocaleString() : "Unknown"}
                    </span>
                  </div>
                </div>

                {/* Content Box */}
                <div className="space-y-1">
                  <span className="text-muted-foreground text-[9px] block font-mono">SANITIZED CONTENT</span>
                  <div
                    className="p-2 border border-border/70 rounded-sm bg-background/90 text-[11px] font-mono max-h-36 overflow-y-auto whitespace-pre-wrap select-text text-foreground"
                  >
                    {loadingDetail ? "Refreshing detail..." : selectedMemory.content}
                  </div>
                </div>

                {/* Advisory Notice */}
                <div className="text-[9px] font-mono text-muted-foreground border-t border-border/40 pt-1.5 flex items-center justify-between">
                  <span className="flex items-center gap-1">
                    <ShieldCheck className="h-3 w-3 text-holo" />
                    Advisory historical context
                  </span>
                  <button
                    type="button"
                    onClick={() => handleDeleteMemory(selectedMemory.memory_id)}
                    className="text-red-400 hover:text-red-300 font-mono text-[9px] flex items-center gap-1 transition-colors px-1.5 py-0.5 border border-red-500/30 rounded"
                  >
                    <Trash2 className="h-2.5 w-2.5" />
                    DELETE MEMORY
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* 3. USER PREFERENCES TAB                                      */}
      {/* ============================================================ */}
      {activeTab === "preferences" && (
        <div className="space-y-3">
          {/* Add Preference Form */}
          <div className="border border-border/70 p-2.5 bg-background/60 space-y-2">
            <span
              className="text-[10px] text-foreground font-semibold uppercase tracking-wider block"
              style={{ fontFamily: "var(--font-display)" }}
            >
              Add User Preference
            </span>

            <div className="space-y-1.5 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
              <div className="grid grid-cols-2 gap-1.5">
                <input
                  type="text"
                  placeholder="Key (e.g. editor_theme)"
                  maxLength={128}
                  value={newPrefKey}
                  onChange={(e) => setNewPrefKey(e.target.value)}
                  className="bg-background border border-border/60 px-2 py-1 rounded-sm focus:outline-none focus:border-holo"
                />
                <input
                  type="text"
                  placeholder="Optional Project ID"
                  value={newPrefProject}
                  onChange={(e) => setNewPrefProject(e.target.value)}
                  className="bg-background border border-border/60 px-2 py-1 rounded-sm focus:outline-none focus:border-holo"
                />
              </div>
              <input
                type="text"
                placeholder="Value (e.g. monokai_pro)"
                maxLength={2048}
                value={newPrefValue}
                onChange={(e) => setNewPrefValue(e.target.value)}
                className="w-full bg-background border border-border/60 px-2 py-1 rounded-sm focus:outline-none focus:border-holo"
              />

              <div className="flex items-center justify-between pt-1">
                <span className="text-[9px] text-muted-foreground">
                  Forbidden keys (passwords, tokens, credentials) will be rejected.
                </span>
                <button
                  type="button"
                  onClick={handleSavePreference}
                  disabled={savingPref || !newPrefKey.trim() || !newPrefValue.trim()}
                  className="px-2.5 py-1 bg-signal/20 text-signal border border-signal/40 hover:bg-signal/30 rounded-sm text-[10px] flex items-center gap-1 transition-colors"
                >
                  <Plus className="h-3 w-3" />
                  {savingPref ? "SAVING..." : "SAVE PREFERENCE"}
                </button>
              </div>
            </div>
          </div>

          {/* Preferences List */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              <span>ACTIVE PREFERENCES ({preferences.length})</span>
              <button
                type="button"
                onClick={() => loadPreferences()}
                className="hover:text-holo flex items-center gap-1"
              >
                <RefreshCw className={`h-2.5 w-2.5 ${prefsLoading ? "animate-spin" : ""}`} />
                REFRESH
              </button>
            </div>

            {preferences.length === 0 ? (
              <div className="border border-dashed border-border/60 p-4 text-center">
                <p className="text-muted-foreground text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
                  No preferences stored. Add preferences when you want RYVEN to remember your workflow choices.
                </p>
              </div>
            ) : (
              <div className="space-y-1.5 max-h-56 overflow-y-auto pr-0.5">
                {preferences.map((pref) => (
                  <div
                    key={`${pref.project_id || "global"}:${pref.key}`}
                    className="p-2 border border-border/60 rounded-sm bg-background/70 flex items-center justify-between gap-2"
                  >
                    <div className="space-y-0.5 min-w-0 flex-1" style={{ fontFamily: "var(--font-mono)" }}>
                      <div className="flex items-center gap-1.5">
                        <Key className="h-3 w-3 text-signal shrink-0" />
                        <span className="text-foreground font-semibold text-[11px] truncate">
                          {pref.key}
                        </span>
                        <span className="text-[9px] text-muted-foreground border border-border/40 px-1 rounded">
                          {pref.project_id || "GLOBAL"}
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground truncate pl-4.5">
                        {pref.value}
                      </p>
                    </div>

                    <button
                      type="button"
                      onClick={() => handleDeletePreference(pref.key, pref.project_id)}
                      className="text-red-400 hover:text-red-300 p-1 transition-colors"
                      title="Forget preference"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* 4. PURGE & DESTRUCTIVE ACTIONS TAB                           */}
      {/* ============================================================ */}
      {activeTab === "purge" && (
        <div className="space-y-3.5">
          {/* Task Memory Deletion */}
          <div className="border border-border/70 p-2.5 bg-background/60 space-y-2">
            <span
              className="text-[10px] text-foreground font-semibold uppercase tracking-wider block"
              style={{ fontFamily: "var(--font-display)" }}
            >
              Task Memory Index Purge
            </span>
            <p className="text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              Removes the memory index entries for a specific task. Authoritative task execution history in TaskPersistenceRepository is strictly preserved.
            </p>

            <div className="flex gap-1.5 pt-1">
              <input
                type="text"
                placeholder="Task ID (e.g. task-9b4f2c01)"
                value={purgeTaskId}
                onChange={(e) => setPurgeTaskId(e.target.value)}
                className="flex-1 bg-background border border-border/60 px-2 py-1 text-[10px] font-mono rounded-sm focus:outline-none focus:border-holo"
              />
              <button
                type="button"
                onClick={handleDeleteTaskMemories}
                disabled={purging || !purgeTaskId.trim()}
                className="px-2.5 py-1 bg-red-500/20 text-red-300 border border-red-500/40 hover:bg-red-500/30 rounded-sm text-[10px] font-mono flex items-center gap-1 transition-colors"
              >
                <Trash2 className="h-3 w-3" />
                PURGE TASK
              </button>
            </div>
          </div>

          {/* Project Bulk Purge (Confirmation Enforced) */}
          <div className="border border-red-500/30 p-2.5 bg-red-500/5 space-y-2">
            <div className="flex items-center gap-1.5 text-red-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span
                className="text-[10px] font-semibold uppercase tracking-wider"
                style={{ fontFamily: "var(--font-display)" }}
              >
                Destructive Project Purge
              </span>
            </div>
            <p className="text-[10px] text-red-200/80" style={{ fontFamily: "var(--font-mono)" }}>
              Permanently removes all semantic and episodic memories associated with a project. Requires authoritative ConfirmationManager approval token.
            </p>

            <div className="flex gap-1.5 pt-1">
              <input
                type="text"
                placeholder="Project ID to purge..."
                value={purgeProjectId}
                onChange={(e) => setPurgeProjectId(e.target.value)}
                className="flex-1 bg-background border border-border/60 px-2 py-1 text-[10px] font-mono rounded-sm focus:outline-none focus:border-red-400"
              />
              <button
                type="button"
                onClick={() => setShowProjectConfirmModal(true)}
                disabled={!purgeProjectId.trim()}
                className="px-2.5 py-1 bg-red-600/30 text-red-200 border border-red-500/60 hover:bg-red-600/50 rounded-sm text-[10px] font-mono flex items-center gap-1 transition-colors"
              >
                <Lock className="h-3 w-3" />
                INITIATE PURGE
              </button>
            </div>
          </div>

          {/* Confirmation Modal */}
          {showProjectConfirmModal && (
            <div className="holo-corners border border-red-500/80 p-3 bg-background/95 space-y-2.5 shadow-lg">
              <div className="flex items-center justify-between border-b border-red-500/30 pb-1.5">
                <span className="text-[11px] font-semibold text-red-300 font-mono">
                  CONFIRM PROJECT PURGE
                </span>
                <button
                  type="button"
                  onClick={() => setShowProjectConfirmModal(false)}
                  className="text-muted-foreground hover:text-foreground text-[10px]"
                >
                  <XCircle className="h-3 w-3" />
                </button>
              </div>

              <p className="text-[10px] text-foreground font-mono">
                Delete all memories for project <span className="text-red-400 font-bold">{purgeProjectId}</span>? This action cannot be undone.
              </p>

              <div className="space-y-1 font-mono text-[10px]">
                <label className="text-muted-foreground text-[9px] block">
                  CONFIRMATION TOKEN (from authorization plane)
                </label>
                <input
                  type="text"
                  placeholder="CONF-XXXXX (Leave blank to request token / trigger 409 check)"
                  value={purgeConfirmationToken}
                  onChange={(e) => setPurgeConfirmationToken(e.target.value)}
                  className="w-full bg-background border border-border/70 px-2 py-1 text-[10px] rounded-sm focus:outline-none focus:border-red-400"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-1 font-mono text-[10px]">
                <button
                  type="button"
                  onClick={() => setShowProjectConfirmModal(false)}
                  className="px-2 py-1 border border-border/60 hover:bg-muted/20 rounded-sm text-muted-foreground"
                >
                  CANCEL
                </button>
                <button
                  type="button"
                  onClick={handleDeleteProjectMemories}
                  disabled={purging}
                  className="px-3 py-1 bg-red-600 text-white rounded-sm hover:bg-red-700 flex items-center gap-1"
                >
                  {purging ? "PURGING..." : "CONFIRM PURGE"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ============================================================ */}
      {/* 5. EXPORT TAB                                                */}
      {/* ============================================================ */}
      {activeTab === "export" && (
        <div className="space-y-3">
          <div className="border border-border/70 p-2.5 bg-background/60 space-y-2">
            <span
              className="text-[10px] text-foreground font-semibold uppercase tracking-wider block"
              style={{ fontFamily: "var(--font-display)" }}
            >
              Sanitized Memory Export
            </span>
            <p className="text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              Generates a verified, sanitized export payload. Secrets, tokens, cookies, &lt;think&gt; blocks, and raw media are strictly excluded.
            </p>

            <button
              type="button"
              onClick={handleExport}
              disabled={exporting}
              className="px-3 py-1.5 bg-holo/20 text-holo border border-holo/40 hover:bg-holo/30 rounded-sm text-[10px] font-mono flex items-center gap-1.5 transition-colors"
            >
              <Download className={`h-3.5 w-3.5 ${exporting ? "animate-spin" : ""}`} />
              {exporting ? "GENERATING EXPORT..." : "GENERATE EXPORT"}
            </button>
          </div>

          {exportData && (
            <div className="border border-border/70 p-2.5 bg-background/80 space-y-2">
              <div className="flex items-center justify-between text-[10px] font-mono border-b border-border/40 pb-1.5">
                <span className="text-signal flex items-center gap-1">
                  <CheckCircle2 className="h-3 w-3" />
                  {exportData.total_exported} memories sanitized
                </span>
                <span className="text-muted-foreground">
                  {new Date(exportData.exported_at).toLocaleTimeString()}
                </span>
              </div>

              <div className="text-[9px] font-mono text-muted-foreground border border-border/40 p-2 rounded bg-background/50 max-h-36 overflow-y-auto">
                <pre>{JSON.stringify(exportData.memories.slice(0, 3), null, 2)}</pre>
                {exportData.memories.length > 3 && (
                  <span className="text-holo mt-1 block">
                    ... and {exportData.memories.length - 3} more items
                  </span>
                )}
              </div>

              <button
                type="button"
                onClick={handleDownloadJSON}
                className="w-full py-1.5 bg-signal/20 text-signal border border-signal/40 hover:bg-signal/30 rounded-sm text-[10px] font-mono flex items-center justify-center gap-1.5 transition-colors"
              >
                <Download className="h-3.5 w-3.5" />
                DOWNLOAD JSON EXPORT
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );

  if (standalone) {
    return (
      <HUDPanel
        title="Memory Control Plane"
        code="MEM.M17.9"
        className="w-[420px] max-h-[88vh] overflow-y-auto"
        tilt={-3}
      >
        {content}
      </HUDPanel>
    );
  }

  return content;
}
