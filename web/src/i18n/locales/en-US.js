// English messages (en-US)
export default {
  common: {
    appName: 'AI Knowledge Base',
    retry: 'Retry',
    cancel: 'Cancel',
    confirm: 'Confirm',
    create: 'Create',
    loading: 'Loading...',
    switchLanguage: 'Switch language'
  },
  nav: {
    newChat: 'New Chat',
    extensions: 'Knowledge Base & Skills',
    knowledgeBase: 'Knowledge Base',
    agentManage: 'Agent Management',
    dashboard: 'Data Overview',
    knowledgeGaps: 'Knowledge Gaps',
    knowledgeBrowser: 'Global Search'
  },
  layout: {
    taskCenter: 'Task Center',
    globalSearch: 'Global Search',
    expandSidebar: 'Expand sidebar',
    collapseSidebar: 'Collapse sidebar',
    debugPanel: 'Debug Panel'
  },
  user: {
    menu: {
      docs: 'Docs',
      themeLight: 'Switch to light mode',
      themeDark: 'Switch to dark mode',
      debug: 'Debug Panel (non-production)',
      settings: 'Settings',
      logout: 'Sign out',
      login: 'Log in'
    },
    role: {
      superadmin: 'Super Admin',
      admin: 'Admin',
      user: 'User',
      unknown: 'Unknown'
    },
    loggedOut: 'Signed out'
  },
  login: {
    serverErrorTitle: 'Server connection failed',
    initTitle: 'System initialization, please create a super admin',
    welcome: 'Welcome',
    label: {
      uid: 'Username',
      phone: 'Phone',
      password: 'Password',
      confirmPassword: 'Confirm Password'
    },
    placeholder: {
      uid: 'Enter username',
      phone: 'Enter phone number',
      loginId: 'Enter username or phone',
      password: 'Enter password'
    },
    validation: {
      uidRequired: 'Please enter a username',
      uidPattern: 'Username can only contain letters, digits and underscores',
      uidLength: 'Username must be 3-20 characters',
      passwordRequired: 'Please enter a password',
      confirmRequired: 'Please confirm your password',
      phoneInvalid: 'Please enter a valid phone number',
      passwordMismatch: 'The two passwords do not match'
    },
    agreement: {
      consent: 'I have read and agree to the',
      user: 'User Agreement',
      privacy: 'Privacy Policy',
      notAccepted: 'Please agree to the User Agreement and Privacy Policy first'
    },
    initSubmit: 'Create admin account',
    submit: 'Log in',
    locked: 'Account locked, try again in {time}',
    oidc: {
      or: 'or',
      button: 'Enterprise SSO'
    },
    success: 'Logged in successfully',
    errors: {
      lockedMessage: 'Account locked',
      locked: 'Account has been locked',
      badCredentials: 'Login failed, check your username and password',
      oidcUrl: 'Unable to get login URL',
      oidcFailed: 'SSO login failed',
      initFailed: 'Initialization failed',
      systemError: 'System error',
      serverAbnormal: 'Server abnormal, please try again later',
      cannotConnect: 'Unable to connect to the server'
    },
    lock: {
      seconds: '{s}s',
      minutes: '{m}m {s}s',
      hours: '{h}h {m}m',
      days: '{d}d {h}h'
    }
  },
  home: {
    loading: 'Connecting to the server...',
    startExperience: 'Get Started',
    flow: {
      agent: 'Agent Harness',
      rag: 'RAG Engine',
      kb: 'Knowledge Base',
      caption: 'Knowledge-base driven Q&A flow'
    },
    errors: {
      unavailable: 'Service unavailable',
      connectionFailed: 'Connection failed',
      backendDown: 'Backend service is not running'
    }
  },
  agent: {
    builtin: 'Built-in',
    threadBoundHint: 'This conversation is bound to an agent. Start a new one to switch.',
    manage: 'Manage agents',
    defaultLabel: 'Agent',
    iconAlt: '{name} icon',
    errors: {
      threadBound: 'This conversation is bound to an agent',
      switchFailed: 'Failed to switch agent',
      selectFirst: 'Please select an agent first',
      openConfigFailed: 'Failed to open configuration'
    }
  },
  db: {
    title: 'Knowledge Base',
    viewSwitchLabel: 'Knowledge base view switch',
    tabs: {
      documents: 'Document Knowledge Bases'
    },
    searchPlaceholder: 'Search knowledge bases...',
    allCategories: 'All Categories',
    manageCategories: 'Manage Categories',
    newDatabase: 'New Knowledge Base',
    modalTitle: 'New Knowledge Base',
    section: {
      type: 'Type',
      category: 'Category',
      name: 'Name',
      embedding: 'Embedding Model',
      chunkPreset: 'Chunk Preset',
      description: 'Knowledge Base Description',
      sharing: 'Sharing',
      typeDesc: 'In agent workflows, this description is used as the tool description. The agent picks a knowledge base based on its title and description, so the more detailed it is, the easier it is for the agent to choose the right one.'
    },
    placeholder: {
      category: 'Select a category',
      name: 'Enter a knowledge base name',
      embedding: 'Select an embedding model'
    },
    empty: {
      title: 'No knowledge bases yet',
      admin: 'Create a knowledge base to upload files and configure retrieval and graph capabilities.',
      user: 'Once authorized by an admin, you can view and maintain knowledge bases here.'
    },
    create: 'Create Knowledge Base',
    noDescription: 'No description',
    menu: {
      copyId: 'Copy knowledge base ID',
      edit: 'Edit',
      delete: 'Delete'
    },
    deleteConfirm: {
      title: 'Delete Knowledge Base',
      content: 'Delete knowledge base "{name}"? This cannot be undone.',
      ok: 'Delete'
    },
    loading: 'Loading knowledge bases...',
    time: {
      today: 'Created today',
      yesterday: 'Created yesterday',
      days: 'Created {n} days ago',
      weeks: 'Created {n} weeks ago',
      months: 'Created {n} months ago',
      years: 'Created {n} years ago',
      files: '{n} files'
    },
    messages: {
      loadCategoriesFailed: 'Failed to load categories',
      loadTypesFailed: 'Failed to load types',
      typesLoadFailed: 'Type load failed',
      selectCategory: 'Please select a category',
      fieldRequired: 'Please fill in required fields',
      copied: 'Knowledge base ID copied',
      deleted: 'Deleted',
      deleteFailed: 'Delete failed'
    }
  },
  conversation: {
    search: 'Search',
    recent: 'Recent',
    pin: 'Pin',
    unpin: 'Unpin',
    rename: 'Rename',
    delete: 'Delete',
    empty: 'No conversation history',
    loadMore: 'Load more',
    newChat: 'New Chat',
    modal: {
      rename: 'Rename conversation',
      confirm: 'Confirm',
      cancel: 'Cancel',
      emptyTitle: 'Title cannot be empty'
    }
  },
  chat: {
    header: {
      status: 'Status',
      files: 'Files',
      viewStatus: 'View status',
      viewFiles: 'View files'
    },
    loadingMessages: 'Loading messages...',
    newChat: 'New Chat',
    reply: {
      generating: 'Generating response...',
      compressing: 'Compiling the answer...',
      calling: 'Calling a tool...',
      finalizing: 'Finalizing...'
    },
    greeting: {
      '0': 'Hi! I am your AI assistant. How can I help you?',
      '1': 'Welcome back! What would you like to explore today?',
      '2': 'Is there anything I can help you with?',
      '3': 'Hello! Feel free to ask me anything.',
      '4': 'I am here. What do you need?'
    },
    toolStatus: {
      query_kb: 'Querying the knowledge base...',
      list_kbs: 'Fetching the knowledge base list...',
      find_kb_document: 'Finding related documents...',
      open_kb_document: 'Reading a document...',
      get_mindmap: 'Fetching the knowledge mindmap...',
      search_file: 'Searching files...',
      ocr_parse_file: 'Parsing a file...',
      web_search: 'Searching the web...',
      subagent_status: 'Processing a subtask...',
      ask_user_question: 'Asking you a question...',
      present_artifacts: 'Preparing the result...',
      default: 'Calling {name}...'
    },
    errors: {
      createFailed: 'Failed to create conversation',
      cancelSent: 'Cancelled',
      invalidRequest: 'Invalid request',
      threadNotFound: 'Conversation not found',
      runNotFound: 'Task not found'
    }
  }
}
