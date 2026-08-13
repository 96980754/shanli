// 中文文案（zh-CN）
export default {
  common: {
    appName: 'AI 知识库',
    retry: '重试',
    cancel: '取消',
    confirm: '确认',
    create: '创建',
    loading: '加载中...',
    switchLanguage: '切换语言'
  },
  nav: {
    newChat: '创建新对话',
    extensions: '知识库与skills',
    knowledgeBase: '知识库',
    agentManage: '智能体管理',
    dashboard: '数据总览',
    knowledgeGaps: '知识缺口',
    knowledgeBrowser: '全库搜索'
  },
  layout: {
    taskCenter: '任务中心',
    globalSearch: '全库搜索',
    expandSidebar: '展开侧边栏',
    collapseSidebar: '折叠侧边栏',
    debugPanel: '调试面板'
  },
  user: {
    menu: {
      docs: '文档中心',
      themeLight: '切换到浅色模式',
      themeDark: '切换到深色模式',
      debug: '调试面板（非生产环境）',
      settings: '设置',
      logout: '退出登录',
      login: '登录'
    },
    role: {
      superadmin: '超级管理员',
      admin: '管理员',
      user: '普通用户',
      unknown: '未知角色'
    },
    loggedOut: '已退出登录'
  },
  login: {
    serverErrorTitle: '服务端连接失败',
    initTitle: '系统初始化，请创建超级管理员',
    welcome: '欢迎登录',
    label: {
      uid: '用户名',
      phone: '手机号',
      password: '密码',
      confirmPassword: '确认密码'
    },
    placeholder: {
      uid: '请输入用户名',
      phone: '请输入手机号',
      loginId: '请输入用户名或手机号',
      password: '请输入密码'
    },
    validation: {
      uidRequired: '请输入用户名',
      uidPattern: '用户名只能包含字母、数字和下划线',
      uidLength: '用户名长度为 3-20 个字符',
      passwordRequired: '请输入密码',
      confirmRequired: '请再次输入密码',
      phoneInvalid: '请输入正确的手机号',
      passwordMismatch: '两次输入的密码不一致'
    },
    agreement: {
      consent: '我已阅读并同意',
      user: '《用户协议》',
      privacy: '《隐私协议》',
      notAccepted: '请先同意用户协议和隐私协议'
    },
    initSubmit: '创建管理员账户',
    submit: '登录',
    locked: '账户已锁定，请 {time} 后重试',
    oidc: {
      or: '或',
      button: '企业统一登录'
    },
    success: '登录成功',
    errors: {
      lockedMessage: '账户已锁定',
      locked: '账户已被锁定',
      badCredentials: '登录失败，请检查用户名和密码',
      oidcUrl: '无法获取登录地址',
      oidcFailed: '统一登录失败',
      initFailed: '初始化失败',
      systemError: '系统错误',
      serverAbnormal: '服务异常，请稍后重试',
      cannotConnect: '无法连接到服务'
    },
    lock: {
      seconds: '{s} 秒',
      minutes: '{m} 分 {s} 秒',
      hours: '{h} 小时 {m} 分',
      days: '{d} 天 {h} 小时'
    }
  },
  home: {
    loading: '正在连接服务...',
    startExperience: '开始体验',
    flow: {
      agent: '智能体 Harness',
      rag: 'RAG 引擎',
      kb: '知识库',
      caption: '智能体发起检索 · 引擎融合向量与图谱 · 召回知识增强生成'
    },
    errors: {
      unavailable: '服务不可用',
      connectionFailed: '连接失败',
      backendDown: '后端服务未启动'
    }
  },
  agent: {
    builtin: '内置',
    threadBoundHint: '当前对话已绑定智能体，新对话可切换。',
    manage: '管理智能体',
    defaultLabel: '智能体',
    iconAlt: '{name} 图标',
    errors: {
      threadBound: '当前对话已绑定智能体',
      switchFailed: '切换智能体失败',
      selectFirst: '请先选择一个智能体',
      openConfigFailed: '打开配置失败'
    }
  },
  db: {
    title: '知识库',
    viewSwitchLabel: '知识库视图切换',
    tabs: {
      documents: '文档知识库'
    },
    searchPlaceholder: '搜索知识库...',
    allCategories: '全部分类',
    manageCategories: '管理分类',
    newDatabase: '新建知识库',
    modalTitle: '新建知识库',
    section: {
      type: '类型',
      category: '分类',
      name: '名称',
      embedding: '向量模型',
      chunkPreset: '分块预设',
      description: '知识库描述',
      sharing: '共享设置',
      typeDesc: '在智能体流程中，这里的描述会作为工具的描述。智能体会根据知识库的标题和描述来选择合适的工具。所以这里描述的越详细，智能体越容易选择到合适的工具。'
    },
    placeholder: {
      category: '请选择分类',
      name: '请输入知识库名称',
      embedding: '请选择向量模型'
    },
    empty: {
      title: '暂无知识库',
      admin: '创建知识库后，可以上传文件并配置检索、图谱能力。',
      user: '管理员授权后，可在这里查看和维护知识库。'
    },
    create: '创建知识库',
    noDescription: '暂无描述',
    menu: {
      copyId: '复制知识库 ID',
      edit: '编辑',
      delete: '删除'
    },
    deleteConfirm: {
      title: '删除知识库',
      content: '确定删除知识库「{name}」吗？此操作不可恢复。',
      ok: '删除'
    },
    loading: '正在加载知识库...',
    time: {
      today: '今天创建',
      yesterday: '昨天创建',
      days: '{n} 天前创建',
      weeks: '{n} 周前创建',
      months: '{n} 个月前创建',
      years: '{n} 年前创建',
      files: '{n} 文件'
    },
    messages: {
      loadCategoriesFailed: '加载分类失败',
      loadTypesFailed: '加载类型失败',
      typesLoadFailed: '知识库类型加载失败，无法创建知识库',
      selectCategory: '请选择内容分类',
      fieldRequired: '请填写必填字段',
      copied: '知识库 ID 已复制',
      deleted: '已删除',
      deleteFailed: '删除失败'
    }
  },
  conversation: {
    search: '搜索',
    recent: '最近',
    pin: '置顶',
    unpin: '取消置顶',
    rename: '重命名',
    delete: '删除',
    empty: '暂无对话历史',
    loadMore: '加载更多',
    newChat: '新的对话',
    modal: {
      rename: '重命名对话',
      confirm: '确认',
      cancel: '取消',
      emptyTitle: '标题不能为空'
    }
  },
  chat: {
    header: {
      status: '状态',
      files: '文件',
      viewStatus: '查看状态',
      viewFiles: '查看文件'
    },
    loadingMessages: '正在加载消息...',
    newChat: '新的对话',
    reply: {
      generating: '正在生成回复...',
      compressing: '正在整理答案...',
      calling: '正在调用工具...',
      finalizing: '正在完成...'
    },
    greeting: {
      '0': '你好！我是你的智能助手，有什么可以帮你的？',
      '1': '欢迎回来！今天想了解什么？',
      '2': '有什么问题需要我帮你解答吗？',
      '3': '你好！请随时向我提问。',
      '4': '我在呢，有什么需要帮助的？'
    },
    toolStatus: {
      query_kb: '正在查询知识库...',
      list_kbs: '正在获取知识库列表...',
      find_kb_document: '正在查找相关文档...',
      open_kb_document: '正在读取相关文档...',
      get_mindmap: '正在获取知识导图...',
      search_file: '正在搜索文件...',
      ocr_parse_file: '正在解析文件...',
      web_search: '正在搜索网页...',
      subagent_status: '正在处理子任务...',
      ask_user_question: '正在向你提问...',
      present_artifacts: '正在整理结果...',
      default: '正在调用 {name}...'
    },
    errors: {
      createFailed: '创建对话失败',
      cancelSent: '已取消',
      invalidRequest: '请求无效',
      threadNotFound: '对话不存在',
      runNotFound: '任务不存在'
    }
  }
}
