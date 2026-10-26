
(() => {
  "use strict";

  // =====================================================
  // 1. ELEMENTS
  // =====================================================

  const $ = (id) => document.getElementById(id);

  const urlInput = $("url");
  const keyInput = $("key");
  const connectButton = $("connect");
  const refreshButton = $("refresh");
  const statusDot = $("status-dot");
  const connectionStatus = $("connection-status");
  const toolCount = $("count");

  const searchInput = $("search");
  const toolsContainer = $("tools");
  const selectedToolName = $("selected");
  const argsInput = $("args");
  const argumentHint = $("arg-hint");
  const runButton = $("run");
  const outputElement = $("output");
  const resultStatus = $("result-status");
  const resultMessage = $("result");

  const chatMessages = $("chat-messages");
  const chatForm = $("chat-form");
  const chatInput = $("chat-input");
  const chatSendButton = $("chat-send");
  const chatStatus = $("chat-status");

  const serversList = $("servers-list");
  const serversStatus = $("servers-status");
  const refreshServersButton = $("refresh-servers");
  const serverForm = $("server-form");

  let gatewayUrl = "";
  let apiKey = "";
  let connected = false;
  let tools = [];
  let selectedTool = null;
  let busy = false;

  // =====================================================
  // 2. HELPERS
  // =====================================================

  function normalizeUrl(value) {
    return value.trim().replace(/\/+$/, "");
  }

  function errorText(error) {
    return error instanceof Error ? error.message : String(error);
  }

  function showConnection(message, state = "") {
    if (connectionStatus) {
      connectionStatus.textContent = message;
    }

    if (statusDot) {
      statusDot.classList.remove("connected", "error");

      if (state === "connected") {
        statusDot.classList.add("connected");
      }

      if (state === "error") {
        statusDot.classList.add("error");
      }
    }
  }

  function showResultStatus(message, state = "") {
    if (!resultStatus) return;

    resultStatus.textContent = message;
    resultStatus.classList.remove("success", "error");

    if (state) resultStatus.classList.add(state);
  }

  function printOutput(value) {
    if (!outputElement) return;

    if (typeof value === "string") {
      try {
        outputElement.textContent = JSON.stringify(
          JSON.parse(value),
          null,
          2
        );
      } catch {
        outputElement.textContent = value;
      }

      return;
    }

    try {
      outputElement.textContent =
        JSON.stringify(value, null, 2) ?? String(value);
    } catch {
      outputElement.textContent = String(value);
    }
  }

  function setBusy(value) {
    busy = value;

    if (connectButton) connectButton.disabled = value;
    if (refreshButton) refreshButton.disabled = value;

    if (runButton) {
      runButton.disabled = value || !connected || !selectedTool;
    }

    if (chatSendButton) {
      chatSendButton.disabled = value || !connected;
    }
  }

  function updateToolCount() {
    if (toolCount) {
      toolCount.textContent = connected ? String(tools.length) : "—";
    }
  }

  function updateRunButton() {
    if (runButton) {
      runButton.disabled = busy || !connected || !selectedTool;
    }
  }

  // =====================================================
  // 3. GATEWAY API REQUEST
  // =====================================================

  async function gatewayRequest(path, options = {}) {
    // Prefer the key saved in memory during connection.
    // Fall back to the input if the user has entered a key.
    const currentUrl = normalizeUrl(
      urlInput?.value || gatewayUrl
    );

    const currentKey =
      (keyInput?.value || "").trim() || apiKey;

    if (!currentUrl) {
      throw new Error("Gateway URL enter karo.");
    }

    if (!currentKey) {
      throw new Error(
        "API key missing hai. Admin API key enter karke Connect gateway dabao."
      );
    }

    gatewayUrl = currentUrl;
    apiKey = currentKey;

    const response = await fetch(`${gatewayUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
        "X-API-Key": apiKey
      }
    });

    const responseText = await response.text();

    let data = {};

    try {
      data = responseText ? JSON.parse(responseText) : {};
    } catch {
      data = { detail: responseText };
    }

    if (!response.ok) {
      const message =
        data.error ||
        data.detail ||
        data.message ||
        `HTTP ${response.status}`;

      throw new Error(`${response.status}: ${message}`);
    }

    return data;
  }

  // =====================================================
  // 4. CONNECT GATEWAY
  // =====================================================

  async function connectGateway() {
    if (busy) return;

    const enteredUrl = normalizeUrl(urlInput.value);
    const enteredKey = keyInput.value.trim();

    if (!enteredUrl || !enteredKey) {
      showConnection("Enter URL and API key", "error");
      return;
    }

    try {
      new URL(enteredUrl);
    } catch {
      showConnection("Invalid gateway URL", "error");
      return;
    }

    gatewayUrl = enteredUrl;
    apiKey = enteredKey;
    connected = false;
    tools = [];
    selectedTool = null;

    setBusy(true);
    showConnection("Connecting...");

    try {
      const data = await gatewayRequest("/tools");

      tools = extractTools(data);
      connected = true;

      showConnection("Connected", "connected");
      if (chatStatus) chatStatus.textContent = "Gateway connected.";

      renderTools();
      updateToolCount();
      updateRunButton();

      // Load registered MCP servers after connecting.
      // A member key can access tools but may not have admin rights.
      await loadServers();

    } catch (error) {
      connected = false;

      showConnection("Connection failed", "error");
      if (chatStatus) chatStatus.textContent = "Connection failed.";

      if (serversStatus) {
        serversStatus.textContent = errorText(error);
      }

      printOutput(errorText(error));
      showResultStatus("Connection error", "error");

      renderTools();
      updateToolCount();

    } finally {
      setBusy(false);
      updateRunButton();
    }
  }

  function extractTools(data) {
    let list = [];

    if (Array.isArray(data)) {
      list = data;
    } else if (Array.isArray(data.tools)) {
      list = data.tools;
    } else if (Array.isArray(data.result?.tools)) {
      list = data.result.tools;
    }

    return list
      .filter((tool) => tool && typeof tool === "object")
      .map((tool) => ({
        ...tool,
        name: tool.name || tool.tool_name || "unnamed_tool",
        description: tool.description || "No description available.",
        inputSchema:
          tool.inputSchema ||
          tool.input_schema ||
          tool.parameters ||
          { type: "object", properties: {} }
      }));
  }

  // =====================================================
  // 5. TOOL LIST
  // =====================================================

  function renderTools() {
    if (!toolsContainer) return;

    const query = (searchInput?.value || "").trim().toLowerCase();

    const filtered = tools.filter((tool) =>
      `${tool.name} ${tool.description}`.toLowerCase().includes(query)
    );

    toolsContainer.replaceChildren();

    if (!connected) {
      showEmptyTools("Connect your gateway to discover tools.");
      return;
    }

    if (!filtered.length) {
      showEmptyTools(
        query ? "No matching tools found." : "No tools available."
      );
      return;
    }

    filtered.forEach((tool) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tool-item";

      if (selectedTool?.name === tool.name) {
        button.classList.add("selected");
      }

      const title = document.createElement("strong");
      title.textContent = tool.name;

      const description = document.createElement("small");
      description.textContent = tool.description;

      button.append(title, description);

      button.addEventListener("click", () => {
        selectTool(tool);
      });

      toolsContainer.appendChild(button);
    });
  }

  function showEmptyTools(message) {
    const wrapper = document.createElement("div");
    wrapper.className = "empty-state";

    const icon = document.createElement("div");
    icon.className = "empty-state-icon";
    icon.textContent = "◇";

    const text = document.createElement("p");
    text.textContent = message;

    wrapper.append(icon, text);
    toolsContainer.appendChild(wrapper);
  }

  function selectTool(tool) {
    selectedTool = tool;

    if (selectedToolName) {
      selectedToolName.textContent = tool.name;
    }

    const schema = tool.inputSchema || {};
    const properties = schema.properties || {};
    const defaults = {};

    Object.entries(properties).forEach(([key, definition]) => {
      if (Object.hasOwn(definition, "default")) {
        defaults[key] = definition.default;
      }
    });

    if (argsInput) {
      argsInput.value = JSON.stringify(defaults, null, 2);
    }

    const required = Array.isArray(schema.required)
      ? schema.required
      : [];

    if (argumentHint) {
      argumentHint.textContent =
        `Required fields: ${required.join(", ") || "None specified"}. Enter valid JSON.`;
    }

    if (outputElement) {
      outputElement.textContent = "Ready to execute this tool.";
    }

    if (resultMessage) resultMessage.textContent = "";

    showResultStatus("Ready");
    renderTools();
    updateRunButton();
  }

  // =====================================================
  // 6. RUN SELECTED TOOL
  // =====================================================

  async function runSelectedTool() {
    if (!connected || !selectedTool || busy) return;

    let args;

    try {
      args = JSON.parse(argsInput.value || "{}");

      if (
        args === null ||
        Array.isArray(args) ||
        typeof args !== "object"
      ) {
        throw new Error("Arguments JSON object hona chahiye.");
      }
    } catch (error) {
      printOutput(errorText(error));
      showResultStatus("Invalid JSON", "error");
      return;
    }

    setBusy(true);
    showResultStatus("Running");

    if (outputElement) outputElement.textContent = "Running tool...";
    if (resultMessage) resultMessage.textContent = "";

    try {
      const data = await gatewayRequest("/call", {
        method: "POST",
        body: JSON.stringify({
          name: selectedTool.name,
          arguments: args
        })
      });

      printOutput(data);

      if (data.isError) {
        showResultStatus("Tool error", "error");
        if (resultMessage) {
          resultMessage.textContent = "Tool reported an error.";
        }
      } else {
        showResultStatus("Completed", "success");
        if (resultMessage) {
          resultMessage.textContent = "Tool execution completed.";
        }
      }

    } catch (error) {
      printOutput(errorText(error));
      showResultStatus("Failed", "error");

      if (resultMessage) {
        resultMessage.textContent = "Check the gateway and arguments.";
      }

    } finally {
      setBusy(false);
      updateRunButton();
    }
  }

  // =====================================================
  // 7. MCP SERVER LIST
  // =====================================================

  async function loadServers() {
    console.log("loadServers() called");
    console.log("serversStatus found:", !!serversStatus);
    console.log("serversList found:", !!serversList);
    console.log("connected:", connected);

    if (!serversStatus || !serversList) {
      console.error("MCP server list HTML elements are missing.");
      return;
    }

    if (!connected) {
      serversStatus.textContent =
        "Connect the gateway using an admin API key first.";
      return;
    }

    serversStatus.textContent = "Loading registered servers...";

    if (refreshServersButton) {
      refreshServersButton.disabled = true;
    }

    try {
      const data = await gatewayRequest("/admin/servers");
      const servers = Array.isArray(data.servers) ? data.servers : [];

      serversList.replaceChildren();

      if (servers.length === 0) {
        serversStatus.textContent = "No MCP servers registered.";
        return;
      }

      serversStatus.textContent =
        `${servers.length} registered MCP server(s).`;

      servers.forEach((server) => {
        const card = document.createElement("div");
        card.className = "stat-card";
        card.style.marginTop = "12px";

        const title = document.createElement("h3");
        title.textContent = server.name || "Unnamed server";

        const address = document.createElement("p");
        address.className = "muted";
        address.textContent = server.url || "";

        const description = document.createElement("p");
        description.textContent =
          server.description || "No description";

        const enabled =
          server.enabled === true || Number(server.enabled) === 1;

        const status = document.createElement("p");
        status.textContent = enabled
          ? "Status: Enabled"
          : "Status: Disabled";

        const actions = document.createElement("div");
        actions.style.display = "flex";
        actions.style.flexWrap = "wrap";
        actions.style.gap = "8px";
        actions.style.marginTop = "12px";

        const toggleButton = document.createElement("button");
        toggleButton.type = "button";
        toggleButton.className = "btn btn-secondary";
        toggleButton.textContent = enabled ? "Disable" : "Enable";

        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "btn btn-secondary";
        deleteButton.textContent = "Delete";

        // ENABLE / DISABLE
        toggleButton.addEventListener("click", async () => {
          toggleButton.disabled = true;
          deleteButton.disabled = true;

          serversStatus.textContent = enabled
            ? `Disabling ${server.name}...`
            : `Enabling ${server.name}...`;

          try {
            await gatewayRequest(
              `/admin/servers/${encodeURIComponent(server.id)}`,
              {
                method: "PATCH",
                body: JSON.stringify({ enabled: !enabled })
              }
            );

            await loadServers();

          } catch (error) {
            serversStatus.textContent =
              `Enable/disable failed: ${errorText(error)}`;

            toggleButton.disabled = false;
            deleteButton.disabled = false;
          }
        });

        // DELETE SERVER
        deleteButton.addEventListener("click", async () => {
          const confirmed = window.confirm(
            `Delete "${server.name}" from the gateway registry?`
          );

          if (!confirmed) return;

          toggleButton.disabled = true;
          deleteButton.disabled = true;

          serversStatus.textContent = `Deleting ${server.name}...`;

          try {
            await gatewayRequest(
              `/admin/servers/${encodeURIComponent(server.id)}`,
              { method: "DELETE" }
            );

            await loadServers();

          } catch (error) {
            serversStatus.textContent =
              `Delete failed: ${errorText(error)}`;

            toggleButton.disabled = false;
            deleteButton.disabled = false;
          }
        });

        actions.append(toggleButton, deleteButton);
        card.append(title, address, description, status, actions);

        serversList.appendChild(card);
      });

    } catch (error) {
      serversStatus.textContent =
        `Could not load servers: ${errorText(error)}`;

      serversList.replaceChildren();

    } finally {
      if (refreshServersButton) {
        refreshServersButton.disabled = false;
      }
    }
  }

  // =====================================================
  // 8. REGISTER MCP SERVER
  // =====================================================

  if (serverForm) {
    serverForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      if (!connected) {
        serversStatus.textContent =
          "Connect the gateway first using an admin API key.";
        return;
      }

      const name = $("server-name")?.value.trim() || "";
      const url = $("server-url")?.value.trim() || "";
      const description =
        $("server-description")?.value.trim() || "";

      if (!name || !url) {
        serversStatus.textContent = "Name and URL are required.";
        return;
      }

      try {
        const parsedUrl = new URL(url);

        if (!["http:", "https:"].includes(parsedUrl.protocol)) {
          throw new Error("Use an HTTP or HTTPS URL.");
        }
      } catch (error) {
        serversStatus.textContent =
          `Invalid server URL: ${errorText(error)}`;
        return;
      }

      const submitButton = serverForm.querySelector(
        'button[type="submit"]'
      );

      if (submitButton) submitButton.disabled = true;

      serversStatus.textContent = "Registering MCP server...";

      try {
        await gatewayRequest("/admin/servers", {
          method: "POST",
          body: JSON.stringify({
            name,
            url,
            description
          })
        });

        serversStatus.textContent = "Server registered successfully.";
        serverForm.reset();

        await loadServers();

      } catch (error) {
        serversStatus.textContent =
          `Registration failed: ${errorText(error)}`;

      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    });
  }

  if (refreshServersButton) {
  refreshServersButton.addEventListener("click", async () => {
    console.log("Refresh servers clicked");

    refreshServersButton.disabled = true;
    const oldText = refreshServersButton.textContent;
    refreshServersButton.textContent = "Refreshing...";

    try {
      await loadServers();
      console.log("Server list refresh completed");
    } catch (error) {
      console.error("Server refresh failed:", error);

      if (serversStatus) {
        serversStatus.textContent =
          `Refresh failed: ${error.message}`;
      }
    } finally {
      refreshServersButton.disabled = false;
      refreshServersButton.textContent = oldText;
    }
  });
}

  // =====================================================
  // 9. AI ASSISTANT
  // =====================================================

  function addChatMessage(role, message) {
    if (!chatMessages) return;

    const row = document.createElement("div");

    row.className = role === "user"
      ? "chat-message user-message"
      : "chat-message assistant-message";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "Y" : "✳";

    const content = document.createElement("div");
    content.className = "message-content";

    const author = document.createElement("p");
    author.className = "message-author";
    author.textContent = role === "user" ? "You" : "Urumi Assistant";

    const text = document.createElement("p");
    text.textContent = message;
    text.style.whiteSpace = "pre-wrap";
    text.style.overflowWrap = "anywhere";

    content.append(author, text);
    row.append(avatar, content);
    chatMessages.appendChild(row);

    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function findTool(...terms) {
    return tools.find((tool) =>
      terms.some((term) => tool.name.toLowerCase().includes(term))
    );
  }

  function defaultArguments(tool) {
    const schema = tool.inputSchema || {};
    const properties = schema.properties || {};
    const args = {};

    Object.entries(properties).forEach(([key, value]) => {
      if (Object.hasOwn(value, "default")) {
        args[key] = value.default;
      }
    });

    return args;
  }

  async function callToolForChat(tool, args) {
    const data = await gatewayRequest("/call", {
      method: "POST",
      body: JSON.stringify({
        name: tool.name,
        arguments: args
      })
    });

    if (data.isError) {
      throw new Error(formatChatResult(data));
    }

    return formatChatResult(data);
  }

  function formatChatResult(data) {
    if (data?.structuredContent) {
      return JSON.stringify(data.structuredContent, null, 2);
    }

    if (Array.isArray(data?.content)) {
      const textParts = data.content
        .filter((item) => item.type === "text")
        .map((item) => item.text);

      if (textParts.length) return textParts.join("\n");
    }

    return typeof data === "string"
      ? data
      : JSON.stringify(data, null, 2);
  }

 
async function handleChatRequest(message) {
  if (!connected) {
    return "Please connect your gateway first.";
  }

  const data = await gatewayRequest("/agent", {
    method: "POST",
    body: JSON.stringify({
      message: message.trim()
    })
  });

  const answer =
    typeof data.answer === "string" && data.answer.trim()
      ? data.answer
      : "The AI agent returned no answer.";

  const usedTools = Array.isArray(data.tools_used)
    ? data.tools_used
    : [];

  if (usedTools.length > 0) {
    return `${answer}\n\nTools used:\n${usedTools
      .map((name) => `• ${name}`)
      .join("\n")}`;
  }

  return answer;
}

  async function sendChatMessage(message) {
    const cleanMessage = message.trim();

    if (!cleanMessage || busy) return;

    addChatMessage("user", cleanMessage);
    chatInput.value = "";

    setBusy(true);

    if (chatStatus) chatStatus.textContent = "Working...";

    try {
      const response = await handleChatRequest(cleanMessage);
      addChatMessage("assistant", response);

      if (chatStatus) chatStatus.textContent = "Ready";

    } catch (error) {
      addChatMessage(
        "assistant",
        `Request failed: ${errorText(error)}`
      );

      if (chatStatus) chatStatus.textContent = "Request failed";

    } finally {
      setBusy(false);
      updateRunButton();
      chatInput.focus();
    }
  }

  // =====================================================
  // 10. EVENT LISTENERS
  // =====================================================

  if (connectButton) {
    connectButton.addEventListener("click", connectGateway);
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      if (!connected || busy) {
        if (!connected) await connectGateway();
        return;
      }

      setBusy(true);

      try {
        const data = await gatewayRequest("/tools");
        tools = extractTools(data);

        renderTools();
        updateToolCount();

        if (selectedTool) {
          selectedTool =
            tools.find((tool) => tool.name === selectedTool.name) || null;
        }

        updateRunButton();

      } catch (error) {
        printOutput(errorText(error));
        showResultStatus("Refresh failed", "error");

      } finally {
        setBusy(false);
        updateRunButton();
      }
    });
  }

  if (searchInput) {
    searchInput.addEventListener("input", renderTools);
  }

  if (runButton) {
    runButton.addEventListener("click", runSelectedTool);
  }

  if (chatForm) {
    chatForm.addEventListener("submit", (event) => {
      event.preventDefault();
      sendChatMessage(chatInput.value);
    });
  }

  // =====================================================
  // 11. INITIAL STATE
  // =====================================================

  showConnection("Not connected");

  updateToolCount();
  updateRunButton();
  showResultStatus("Waiting");

  if (outputElement) {
    outputElement.textContent =
      "Connect your gateway and select a tool to begin.";
  }

  if (serversStatus) {
    serversStatus.textContent =
      "Connect using an admin API key to load registered servers.";
  }

  if (chatStatus) {
    chatStatus.textContent = "Connect your gateway to begin.";
  }

})();
