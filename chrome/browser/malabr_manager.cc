 #include "chrome/browser/malabr_manager.h"

#include <arpa/inet.h>

#include <algorithm>

#include <utility>

#include "base/command_line.h"
#include "base/environment.h"
#include "base/logging.h"
#include "base/path_service.h"
#include "base/process/launch.h"
#include "base/strings/string_number_conversions.h"
#include "base/rand_util.h"
#include "base/task/thread_pool.h"
#include "content/public/browser/browser_thread.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/profiles/profile_manager.h"
#include "chrome/browser/ui/browser.h"
#include "chrome/browser/ui/browser_list.h"
#include "chrome/browser/ui/tabs/tab_strip_model.h"
#include "chrome/common/chrome_features.h"
#include "components/sessions/content/session_tab_helper.h"
#include "extensions/common/extension.h"
#include "content/public/browser/visibility.h"
#include "content/public/browser/web_contents.h"
#include "net/base/net_errors.h"

#if BUILDFLAG(IS_LINUX)
#include "build/build_config.h"
#endif

namespace {

const char kMServerUDSPath[] = "addition_malabr/mserver/app.py";

// Header route that tells the server "this connection is the control channel,
// hand it to the reader thread -- do not treat it as a generate() request".
const char kControlRoute[] = "ROUTE_MALABR_CONTROL";

// No tab is foreground: no active window, or the control connection is down
// and we have deliberately degraded to uniform treatment (section 5f).
constexpr int kNoForegroundTab = -1;

}  // namespace

namespace malabr {

ControlConnection::ControlConnection() = default;

ControlConnection::~ControlConnection() = default;

bool ControlConnection::WriteFrame(const std::string& message) {
  // [4B big-endian length][ASCII message] -- same framing as the request
  // header, so the Python side reuses recv_full() unchanged.
  uint32_t len_net = htonl(static_cast<uint32_t>(message.size()));

  std::string frame;
  frame.reserve(sizeof(len_net) + message.size());
  frame.append(reinterpret_cast<const char*>(&len_net), sizeof(len_net));
  frame.append(message);

  size_t written_total = 0;
  while (written_total < frame.size()) {
    int written = socket_->Write(frame.data() + written_total,
                                 frame.size() - written_total);
    if (written <= 0) {
      return false;
    }
    written_total += written;
  }
  return true;
}

bool ControlConnection::EnsureConnected(
    const std::vector<std::string>& resync) {
  if (socket_ && socket_->IsConnected()) {
    return true;
  }

  socket_ = std::make_unique<extensions::MSocketUDS>(
      extensions::GetMalabrSocketPath());
  if (socket_->Connect() != net::OK) {
    // Server not up yet, or restarting. Dropped silently on purpose: this is
    // the expected state during browser startup before the ~3s model load,
    // and the next control event retries. No backoff timer needed -- control
    // events are user-driven and infrequent.
    socket_.reset();
    return false;
  }

  // Standard 5-field header, with the control route. payload_size is 0: the
  // control channel carries no request body, only pushes.
  std::string header = std::string(kControlRoute) + ",,-1,background,0";
  uint32_t hdr_len_net = htonl(static_cast<uint32_t>(header.size()));
  std::string hello;
  hello.append(reinterpret_cast<const char*>(&hdr_len_net),
               sizeof(hdr_len_net));
  hello.append(header);

  size_t written_total = 0;
  while (written_total < hello.size()) {
    int written = socket_->Write(hello.data() + written_total,
                                 hello.size() - written_total);
    if (written <= 0) {
      socket_.reset();
      return false;
    }
    written_total += written;
  }

  // RESYNC (section 5f). A reconnect means the server may have diverged from
  // us -- or restarted with no state at all -- and ANY message written into
  // the dead socket was lost. Push current state unconditionally rather than
  // waiting for the user's next tab switch, which might never come.
  //
  // This is state, not replayed events, and that distinction is load-bearing:
  // a TAB_CLOSED lost while disconnected can never be replayed (the tab is
  // already gone, we have no record it existed), so LIVE_TABS lets the server
  // reconcile instead. Pushes naming tabs the server has no session for are
  // already a defined no-op (section 9).
  for (const std::string& message : resync) {
    if (!WriteFrame(message)) {
      socket_.reset();
      return false;
    }
  }
  last_resync_sent_ = resync;

  LOG(INFO) << "MALABR: control connection established, resynced "
            << resync.size() << " state message(s)";
  return true;
}

bool ControlConnection::Connect(std::vector<std::string> resync) {
  return EnsureConnected(resync);
}

void ControlConnection::Send(std::string message,
                             std::vector<std::string> resync) {
  if (!EnsureConnected(resync)) {
    return;
  }

  // If we just handshaked, the resync already carried this exact message
  // (UpdateForegroundTab sends FOREGROUND, and resync leads with FOREGROUND).
  // Sending it twice is harmless but pointless (audit hole 6).
  if (!last_resync_sent_.empty()) {
    for (const std::string& already : last_resync_sent_) {
      if (already == message) {
        last_resync_sent_.clear();
        return;
      }
    }
    last_resync_sent_.clear();
  }

  if (WriteFrame(message)) {
    return;
  }

  // Peer went away mid-write. IsConnected() cannot detect a dead peer -- it
  // only checks the local fd -- so this is the first moment we learn. The
  // message is still in hand, so reconnect and retry it ONCE rather than
  // dropping it and relying on a later event to trigger reconciliation
  // (audit hole 3).
  LOG(WARNING) << "MALABR: control write failed, reconnecting and retrying";
  socket_.reset();
  if (EnsureConnected(resync) && WriteFrame(message)) {
    return;
  }
  LOG(WARNING) << "MALABR: control message dropped after retry: " << message;
  socket_.reset();
}

}  // namespace malabr

MalabrManager& MalabrManager::GetInstance() {
  // NoDestructor, not a plain function-local static: the destructor touches
  // BrowserList, and exit-time destruction order relative to BrowserList is
  // undefined. StopMLServer() runs explicitly at shutdown instead
  // (chrome_browser_main.cc), so nothing is lost (audit hole 2).
  static base::NoDestructor<MalabrManager> instance;
  return *instance;
}

MalabrManager::MalabrManager()
    : control_(base::ThreadPool::CreateSequencedTaskRunner(
          {base::MayBlock(), base::TaskShutdownBehavior::SKIP_ON_SHUTDOWN})) {
  // Observe window activation for the lifetime of the browser. Per-window tab
  // strips are observed as each Browser appears (OnBrowserAdded) -- a single
  // global observer does NOT cover every window (section 5a).
  BrowserList::AddObserver(this);

  // ...but OnBrowserAdded does NOT fire retroactively. This singleton is
  // constructed lazily on first GetInstance(), so any window that already
  // exists at that moment would never have its tab strip observed -- tab
  // closes in it would never reach the server and would leak slots silently.
  // Catch up explicitly rather than relying on startup ordering.
  for (Browser* browser : *BrowserList::GetInstance()) {
    if (browser->tab_strip_model()) {
      browser->tab_strip_model()->AddObserver(this);
    }
  }
  UpdateForegroundTab();

  // ExtensionRegistry is per-profile, so there is no one registry to observe.
  // Catch the profiles that already exist, then stay subscribed for new ones
  // (a second profile window, an incognito session).
  if (ProfileManager* pm = g_browser_process->profile_manager()) {
    pm->AddObserver(this);
    for (Profile* profile : pm->GetLoadedProfiles()) {
      OnProfileAdded(profile);
    }
  }
}

MalabrManager::~MalabrManager() {
  // NEVER RUNS in production: GetInstance() holds this in a
  // base::NoDestructor precisely so no exit-time destructor touches
  // BrowserList in an undefined order (audit hole 2). Shutdown goes through
  // the explicit StopMLServer() call in chrome_browser_main.cc instead.
  //
  // Kept correct anyway, so a future test that constructs one on the stack
  // does not leave dangling observer registrations.
  BrowserList::RemoveObserver(this);
  StopMLServer();
}

// ---------------------------------------------------------------------------
// Server process lifecycle
// ---------------------------------------------------------------------------

void MalabrManager::StartMLServerIfEnabled() {
  if (!base::FeatureList::IsEnabled(features::kMalabrFeature)) {
    return;
  }

#if BUILDFLAG(IS_LINUX)
  base::LaunchOptions options;
  base::FilePath project_root;
  CHECK(base::PathService::Get(base::DIR_CURRENT, &project_root));
  base::FilePath server_script = project_root.AppendASCII(kMServerUDSPath);

  LOG(INFO) << "Server script path: " << server_script.value();

  // The interpreter is configurable, for the same reason the socket path is:
  // "python3" resolves to whatever is first on PATH, and in a Chromium build
  // shell that is the build environment's python -- which has no llama_cpp.
  // app.py then dies instantly with ModuleNotFoundError, no socket is ever
  // created, and every generate() fails with nothing in the UI to explain it.
  // MALABR_PYTHON names the interpreter that actually has the runtime.
  std::string python_bin = "python3";
  {
    std::unique_ptr<base::Environment> env(base::Environment::Create());
    std::string from_env;
    if (env->GetVar("MALABR_PYTHON", &from_env) && !from_env.empty()) {
      python_bin = from_env;
    }
  }
  LOG(INFO) << "MalabrManager: interpreter " << python_bin;

  // Named variable, not a temporary: `CommandLine cmd(FilePath(python_bin))`
  // is the most vexing parse -- with an identifier inside, the compiler reads
  // it as a function declaration. The original took a string literal, which
  // cannot be a parameter name, so this only appeared once the value moved
  // into a variable.
  const base::FilePath python_path(python_bin);
  base::CommandLine mserver_cmd(python_path);
  mserver_cmd.AppendArg("-u");
  mserver_cmd.AppendArg(server_script.value());

  mserver_uds_process_ = base::LaunchProcess(mserver_cmd, options);
  if (!mserver_uds_process_.IsValid()) {
    LOG(ERROR) << "MalabrManager: Failed to launch the mserver";
    return;
  }
  LOG(INFO) << "Model server started, pid=" << mserver_uds_process_.Pid();

  // The server needs ~3s to load the model, so connecting right now would
  // fail. Start the retry loop; it backs off until the server is listening.
  // Without this the control channel is only ever attempted on a tab switch
  // or close, so a user who does neither would run an entire session with it
  // dead -- no foreground updates, no TAB_CLOSED (audit hole 1).
  EnsureControlConnected();
#endif
}

void MalabrManager::StopMLServer() {
#if BUILDFLAG(IS_LINUX)
  if (!mserver_uds_process_.IsValid()) {
    return;
  }
  LOG(INFO) << "Terminating model server, pid=" << mserver_uds_process_.Pid();
  mserver_uds_process_.Terminate(0, false);
#endif
}

// ---------------------------------------------------------------------------
// Control channel
// ---------------------------------------------------------------------------

void MalabrManager::SendControlMessage(std::string message) {
  // Resync payload is computed HERE, on the UI thread, because walking
  // BrowserList/TabStripModel is only legal on the UI thread. The control
  // sequence just replays whatever it is handed.
  //
  // Hops to the blocking sequence: never write to a socket on the UI thread.
  control_.AsyncCall(&malabr::ControlConnection::Send)
      .WithArgs(std::move(message), BuildResyncMessages());
}

void MalabrManager::EnsureControlConnected() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (control_connect_pending_) {
    return;  // a retry is already in flight
  }
  control_connect_pending_ = true;

  control_.AsyncCall(&malabr::ControlConnection::Connect)
      .WithArgs(BuildResyncMessages())
      .Then(base::BindOnce(&MalabrManager::OnControlConnectResult,
                           // Safe: NoDestructor singleton, never destroyed.
                           base::Unretained(this)));
}

void MalabrManager::OnControlConnectResult(bool connected) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  control_connect_pending_ = false;

  if (connected) {
    control_retry_delay_ = base::Milliseconds(500);  // reset for next time
    return;
  }

  // Jittered backoff (section 10): a fixed interval risks several clients
  // retrying in lockstep and bursting the instant the server becomes ready.
  const base::TimeDelta kMaxDelay = base::Seconds(30);
  base::TimeDelta jitter =
      control_retry_delay_ * base::RandDouble() * 0.3;

  content::GetUIThreadTaskRunner({})->PostDelayedTask(
      FROM_HERE,
      base::BindOnce(&MalabrManager::EnsureControlConnected,
                     base::Unretained(this)),
      control_retry_delay_ + jitter);

  control_retry_delay_ = std::min(control_retry_delay_ * 2, kMaxDelay);
}

std::vector<std::string> MalabrManager::BuildResyncMessages() const {
  std::vector<std::string> messages;
  messages.push_back("FOREGROUND," +
                     base::NumberToString(foreground_tab_id_));

  // Every tab that currently exists, across every window. The server frees
  // any session whose tab is NOT in this set -- that is what recovers the
  // slots of tabs closed while the control connection was down, whose
  // TAB_CLOSED messages were lost.
  //
  // Cached: this set changes only when a tab is OPENED or CLOSED, never when
  // the user merely switches tabs -- and switching is by far the more
  // frequent event. Rebuilding it on every control message meant walking
  // every tab in every window for a result that had not changed (hole 5).
  if (live_tabs_dirty_) {
    std::string live = "LIVE_TABS";
    for (Browser* browser : *BrowserList::GetInstance()) {
      TabStripModel* tabs = browser->tab_strip_model();
      if (!tabs) {
        continue;
      }
      for (int i = 0; i < tabs->count(); ++i) {
        content::WebContents* contents = tabs->GetWebContentsAt(i);
        if (!contents) {
          continue;
        }
        int tab_id = sessions::SessionTabHelper::IdForTab(contents).id();
        live += "," + base::NumberToString(tab_id);
      }
    }
    cached_live_tabs_ = live;
    live_tabs_dirty_ = false;
  }
  messages.push_back(cached_live_tabs_);
  return messages;
}

// ---------------------------------------------------------------------------
// Foreground computation -- the whole point of section 5a
// ---------------------------------------------------------------------------

void MalabrManager::UpdateForegroundTab() {
  int new_foreground = kNoForegroundTab;

  // foreground = the active tab OF the active window, and only if that tab is
  // actually visible. Both halves are required:
  //   - visibility alone: two unoccluded windows on two monitors would BOTH
  //     report VISIBLE, so several tabs would claim foreground at once.
  //   - active-window alone: a minimized window can still be last-active,
  //     which Visibility::HIDDEN correctly excludes.
  Browser* active = BrowserList::GetInstance()->GetLastActive();
  if (active && active->tab_strip_model()) {
    content::WebContents* contents =
        active->tab_strip_model()->GetActiveWebContents();
    if (contents &&
        contents->GetVisibility() == content::Visibility::VISIBLE) {
      new_foreground = sessions::SessionTabHelper::IdForTab(contents).id();
    }
  }

  if (new_foreground == foreground_tab_id_) {
    return;  // nothing moved; do not spam the channel
  }
  foreground_tab_id_ = new_foreground;

  // ONE message carrying the whole answer, not a hide+show pair.
  //
  // NOTE this is a TAB id, not a session key. The browser genuinely does not
  // know which extensions hold sessions in a tab, and it should not need to:
  // if a tab is foreground then every session in it is foreground. The
  // server resolves tab_id -> session(s) against its own registry. Storing
  // one tab id also makes "two tabs are foreground at once" unrepresentable,
  // rather than something the protocol has to be careful to avoid
  // (section 5f).
  SendControlMessage("FOREGROUND," +
                     base::NumberToString(foreground_tab_id_));
}

// ---------------------------------------------------------------------------
// BrowserListObserver -- window activation
// ---------------------------------------------------------------------------

void MalabrManager::OnBrowserAdded(Browser* browser) {
  // TabStripModelObserver is per-window, so every window needs its own
  // registration -- one global observer would silently miss all but one.
  if (browser->tab_strip_model()) {
    browser->tab_strip_model()->AddObserver(this);
  }
  live_tabs_dirty_ = true;  // a new window brings its tabs with it
}

void MalabrManager::OnBrowserRemoved(Browser* browser) {
  if (browser->tab_strip_model()) {
    browser->tab_strip_model()->RemoveObserver(this);
  }
  live_tabs_dirty_ = true;  // its tabs went with it
  UpdateForegroundTab();
}

void MalabrManager::OnBrowserSetLastActive(Browser* browser) {
  UpdateForegroundTab();
}

void MalabrManager::OnBrowserNoLongerActive(Browser* browser) {
  UpdateForegroundTab();
}

// ---------------------------------------------------------------------------
// Extension lifecycle -- sweep teardown (section 10)
// ---------------------------------------------------------------------------

void MalabrManager::OnProfileAdded(Profile* profile) {
  extensions::ExtensionRegistry* registry =
      extensions::ExtensionRegistry::Get(profile);
  if (registry && !registry_observations_.IsObservingSource(registry)) {
    registry_observations_.AddObservation(registry);
  }
}

void MalabrManager::OnExtensionUnloaded(
    content::BrowserContext* browser_context,
    const extensions::Extension* extension,
    extensions::UnloadedExtensionReason reason) {
  // Fires for disable AND for update -- extension_registrar.cc calls
  // RemoveExtension(..., UnloadedExtensionReason::UPDATE), so an update takes
  // this same path (section 5c). That is correct: the new version should not
  // inherit the old one's conversations.
  SendControlMessage("EXT_UNLOADED," + extension->id());
}

void MalabrManager::OnExtensionUninstalled(
    content::BrowserContext* browser_context,
    const extensions::Extension* extension,
    extensions::UninstallReason reason) {
  SendControlMessage("EXT_UNLOADED," + extension->id());
}

void MalabrManager::OnShutdown(extensions::ExtensionRegistry* registry) {
  if (registry_observations_.IsObservingSource(registry)) {
    registry_observations_.RemoveObservation(registry);
  }
}

// ---------------------------------------------------------------------------
// TabStripModelObserver -- tab switches and tab closes
// ---------------------------------------------------------------------------

void MalabrManager::OnTabStripModelChanged(
    TabStripModel* tab_strip_model,
    const TabStripModelChange& change,
    const TabStripSelectionChange& selection) {
  // Any structural change to a tab strip invalidates the cached LIVE_TABS.
  if (change.type() == TabStripModelChange::kInserted ||
      change.type() == TabStripModelChange::kRemoved ||
      change.type() == TabStripModelChange::kReplaced) {
    live_tabs_dirty_ = true;
  }

  if (change.type() == TabStripModelChange::kRemoved) {
    for (const auto& removed : change.GetRemove()->contents) {
      // A tab MOVED to another window is not a close -- its session must
      // survive the drag. Only kDeleted means the tab is really going away.
      if (removed.remove_reason !=
          TabStripModelChange::RemoveReason::kDeleted) {
        continue;
      }

      // Always the tab's own id: removed.session_id names the "recently closed"
      // history entry, which the server has never seen.
      int tab_id =
          sessions::SessionTabHelper::IdForTab(removed.contents).id();

      // Tells the server to cancel any generation and free the slot.
      //
      // This is the ONLY delivery path when the session is idle: idle
      // sessions have no open per-request socket to break, so without this
      // message a closed idle tab would hold its slot forever, and
      // n_seq_max closed tabs would exhaust the pool (sections 5e, 6a).
      SendControlMessage("TAB_CLOSED," + base::NumberToString(tab_id));
    }
  }

  // A tab switch within the active window changes which tab is foreground.
  if (selection.active_tab_changed()) {
    UpdateForegroundTab();
  }
}
