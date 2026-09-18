import { useMemo } from 'react'
import { ApiReferenceReact } from '@scalar/api-reference-react'
import type { ApiReferenceConfiguration } from '@scalar/api-reference-react'
import { useTranslation } from 'react-i18next'
import { PublicLayout } from '@/components/layout'
import relaySpec from './relay-openapi.json'
import '@scalar/api-reference-react/style.css'
import './scalar.css'

const API_BASE_URL = 'https://www.openmindapi.com'

type DocsLocale = 'en' | 'zh' | 'fr' | 'ja' | 'ru' | 'vi'

type LocalizedDocs = {
  title: string
  description: string
  tags: Record<string, string>
  operations: Record<string, string>
}

const docsLocales: Record<DocsLocale, LocalizedDocs> = {
  zh: {
    title: 'OpenMind API 接口文档',
    description:
      '面向市场通用场景的 OpenAI-compatible API 网关，支持聊天、Responses、图片、文件、视频、嵌入、音频、重排序、Claude 兼容 Messages 和 Gemini 兼容模型接口。',
    tags: {
      '获取模型列表': '获取模型列表',
      'OpenAI格式(Chat)': 'OpenAI 格式（Chat）',
      'OpenAI格式(Responses)': 'OpenAI 格式（Responses）',
      图片生成: '图片生成',
      '图片生成/OpenAI兼容格式': '图片生成 / OpenAI 兼容格式',
      '图片生成/Qwen千问': '图片生成 / Qwen 千问',
      视频生成: '视频生成',
      '视频生成/Sora兼容格式': '视频生成 / Sora 兼容格式',
      '视频生成/Kling格式': '视频生成 / Kling 格式',
      '视频生成/即梦格式': '视频生成 / 即梦格式',
      'Claude格式(Messages)': 'Claude 格式（Messages）',
      Gemini格式: 'Gemini 格式',
      'OpenAI格式(Embeddings)': 'OpenAI 格式（Embeddings）',
      '文本补全(Completions)': '文本补全（Completions）',
      'OpenAI音频(Audio)': 'OpenAI 音频（Audio）',
      '重排序(Rerank)': '重排序（Rerank）',
      Moderations: '内容审核（Moderations）',
      Realtime: '实时接口（Realtime）',
      未实现: '未实现',
      '未实现/Fine-tunes': '未实现 / Fine-tunes',
      '未实现/Files': '未实现 / Files',
    },
    operations: {
      'GET /v1/models': '获取模型列表',
      'GET /v1beta/models': '获取 Gemini 格式模型列表',
      'POST /v1/chat/completions': '创建聊天对话',
      'POST /v1/responses': '创建响应',
      'POST /v1/responses/compact': '压缩对话',
      'POST /v1/images/generations': '生成图像',
      'POST /v1/images/edits': '编辑图像',
      'POST /v1/videos': '创建视频',
      'GET /v1/videos/{task_id}': '获取视频任务状态',
      'GET /v1/videos/{task_id}/content': '获取视频内容',
      'POST /kling/v1/videos/text2video': 'Kling 文生视频',
      'GET /kling/v1/videos/text2video/{task_id}':
        '获取 Kling 文生视频任务状态',
      'POST /kling/v1/videos/image2video': 'Kling 图生视频',
      'GET /kling/v1/videos/image2video/{task_id}':
        '获取 Kling 图生视频任务状态',
      'POST /jimeng/': '即梦视频生成',
      'POST /v1/video/generations': '创建视频生成任务',
      'GET /v1/video/generations/{task_id}': '获取视频生成任务状态',
      'POST /v1/messages': 'Claude 聊天',
      'POST /v1beta/models/{model}:generateContent': 'Gemini 图片生成',
      'POST /v1/engines/{model}/embeddings': 'Gemini 嵌入',
      'POST /v1/embeddings': '创建文本嵌入',
      'POST /v1/completions': '创建文本补全',
      'POST /v1/audio/transcriptions': '音频转录',
      'POST /v1/audio/translations': '音频翻译',
      'POST /v1/audio/speech': '文本转语音',
      'POST /v1/rerank': '文档重排序',
      'POST /v1/moderations': '内容审核',
      'GET /v1/realtime': '实时 WebSocket 连接',
      'GET /v1/fine-tunes': '列出微调任务（未实现）',
      'POST /v1/fine-tunes': '创建微调任务（未实现）',
      'GET /v1/fine-tunes/{fine_tune_id}': '获取微调任务详情（未实现）',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        '取消微调任务（未实现）',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        '获取微调任务事件（未实现）',
      'GET /v1/files': '列出文件（未实现）',
      'POST /v1/files': '上传文件',
      'GET /v1/files/{file_id}': '获取文件信息',
      'DELETE /v1/files/{file_id}': '删除文件',
      'GET /v1/files/{file_id}/content': '获取文件内容',
    },
  },
  en: {
    title: 'OpenMind API Reference',
    description:
      'OpenAI-compatible API gateway for chat, responses, images, files, videos, embeddings, audio, rerank, Claude-compatible messages, and Gemini-compatible model endpoints.',
    tags: {
      '获取模型列表': 'Models',
      'OpenAI格式(Chat)': 'OpenAI-compatible Chat',
      'OpenAI格式(Responses)': 'OpenAI-compatible Responses',
      图片生成: 'Image Generation',
      '图片生成/OpenAI兼容格式': 'Image Generation / OpenAI-compatible',
      '图片生成/Qwen千问': 'Image Generation / Qwen',
      视频生成: 'Video Generation',
      '视频生成/Sora兼容格式': 'Video Generation / Sora-compatible',
      '视频生成/Kling格式': 'Video Generation / Kling',
      '视频生成/即梦格式': 'Video Generation / Jimeng',
      'Claude格式(Messages)': 'Claude-compatible Messages',
      Gemini格式: 'Gemini-compatible',
      'OpenAI格式(Embeddings)': 'OpenAI-compatible Embeddings',
      '文本补全(Completions)': 'Text Completions',
      'OpenAI音频(Audio)': 'OpenAI-compatible Audio',
      '重排序(Rerank)': 'Rerank',
      Moderations: 'Moderations',
      Realtime: 'Realtime',
      未实现: 'Not Implemented',
      '未实现/Fine-tunes': 'Not Implemented / Fine-tunes',
      '未实现/Files': 'Not Implemented / Files',
    },
    operations: {
      'GET /v1/models': 'List models',
      'GET /v1beta/models': 'List Gemini-format models',
      'POST /v1/chat/completions': 'Create chat completion',
      'POST /v1/responses': 'Create response',
      'POST /v1/responses/compact': 'Compact conversation',
      'POST /v1/images/generations': 'Generate image',
      'POST /v1/images/edits': 'Edit image',
      'POST /v1/videos': 'Create video',
      'GET /v1/videos/{task_id}': 'Get video task status',
      'GET /v1/videos/{task_id}/content': 'Get video content',
      'POST /kling/v1/videos/text2video': 'Kling text to video',
      'GET /kling/v1/videos/text2video/{task_id}':
        'Get Kling text-to-video task status',
      'POST /kling/v1/videos/image2video': 'Kling image to video',
      'GET /kling/v1/videos/image2video/{task_id}':
        'Get Kling image-to-video task status',
      'POST /jimeng/': 'Jimeng video generation',
      'POST /v1/video/generations': 'Create video generation task',
      'GET /v1/video/generations/{task_id}': 'Get video generation task status',
      'POST /v1/messages': 'Claude chat',
      'POST /v1beta/models/{model}:generateContent': 'Gemini image generation',
      'POST /v1/engines/{model}/embeddings': 'Gemini embeddings',
      'POST /v1/embeddings': 'Create embeddings',
      'POST /v1/completions': 'Create text completion',
      'POST /v1/audio/transcriptions': 'Transcribe audio',
      'POST /v1/audio/translations': 'Translate audio',
      'POST /v1/audio/speech': 'Create speech',
      'POST /v1/rerank': 'Rerank documents',
      'POST /v1/moderations': 'Moderate content',
      'GET /v1/realtime': 'Realtime WebSocket connection',
      'GET /v1/fine-tunes': 'List fine-tunes (not implemented)',
      'POST /v1/fine-tunes': 'Create fine-tune (not implemented)',
      'GET /v1/fine-tunes/{fine_tune_id}':
        'Get fine-tune details (not implemented)',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        'Cancel fine-tune (not implemented)',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        'List fine-tune events (not implemented)',
      'GET /v1/files': 'List files (not implemented)',
      'POST /v1/files': 'Upload file',
      'GET /v1/files/{file_id}': 'Get file info',
      'DELETE /v1/files/{file_id}': 'Delete file',
      'GET /v1/files/{file_id}/content': 'Get file content',
    },
  },
  fr: {
    title: 'Référence de l API OpenMind',
    description:
      'Passerelle API compatible OpenAI pour le chat, Responses, les images, les fichiers, la vidéo, les embeddings, l audio, le rerank, les messages compatibles Claude et les endpoints de modèles compatibles Gemini.',
    tags: {
      '获取模型列表': 'Modèles',
      'OpenAI格式(Chat)': 'Chat compatible OpenAI',
      'OpenAI格式(Responses)': 'Responses compatible OpenAI',
      图片生成: 'Génération d images',
      '图片生成/OpenAI兼容格式': 'Génération d images / compatible OpenAI',
      '图片生成/Qwen千问': 'Génération d images / Qwen',
      视频生成: 'Génération vidéo',
      '视频生成/Sora兼容格式': 'Génération vidéo / compatible Sora',
      '视频生成/Kling格式': 'Génération vidéo / Kling',
      '视频生成/即梦格式': 'Génération vidéo / Jimeng',
      'Claude格式(Messages)': 'Messages compatibles Claude',
      Gemini格式: 'Compatible Gemini',
      'OpenAI格式(Embeddings)': 'Embeddings compatibles OpenAI',
      '文本补全(Completions)': 'Complétions de texte',
      'OpenAI音频(Audio)': 'Audio compatible OpenAI',
      '重排序(Rerank)': 'Rerank',
      Moderations: 'Modération',
      Realtime: 'Temps réel',
      未实现: 'Non implémenté',
      '未实现/Fine-tunes': 'Non implémenté / Fine-tunes',
      '未实现/Files': 'Non implémenté / Fichiers',
    },
    operations: {
      'GET /v1/models': 'Lister les modèles',
      'GET /v1beta/models': 'Lister les modèles au format Gemini',
      'POST /v1/chat/completions': 'Créer une conversation chat',
      'POST /v1/responses': 'Créer une réponse',
      'POST /v1/responses/compact': 'Compacter la conversation',
      'POST /v1/images/generations': 'Générer une image',
      'POST /v1/images/edits': 'Modifier une image',
      'POST /v1/videos': 'Créer une vidéo',
      'GET /v1/videos/{task_id}': 'Obtenir le statut de la tâche vidéo',
      'GET /v1/videos/{task_id}/content': 'Obtenir le contenu vidéo',
      'POST /kling/v1/videos/text2video': 'Kling texte vers vidéo',
      'GET /kling/v1/videos/text2video/{task_id}':
        'Obtenir le statut Kling texte vers vidéo',
      'POST /kling/v1/videos/image2video': 'Kling image vers vidéo',
      'GET /kling/v1/videos/image2video/{task_id}':
        'Obtenir le statut Kling image vers vidéo',
      'POST /jimeng/': 'Génération vidéo Jimeng',
      'POST /v1/video/generations': 'Créer une tâche de génération vidéo',
      'GET /v1/video/generations/{task_id}':
        'Obtenir le statut de génération vidéo',
      'POST /v1/messages': 'Chat Claude',
      'POST /v1beta/models/{model}:generateContent':
        'Génération d images Gemini',
      'POST /v1/engines/{model}/embeddings': 'Embeddings Gemini',
      'POST /v1/embeddings': 'Créer des embeddings',
      'POST /v1/completions': 'Créer une complétion de texte',
      'POST /v1/audio/transcriptions': 'Transcrire l audio',
      'POST /v1/audio/translations': 'Traduire l audio',
      'POST /v1/audio/speech': 'Créer une synthèse vocale',
      'POST /v1/rerank': 'Réordonner des documents',
      'POST /v1/moderations': 'Modérer le contenu',
      'GET /v1/realtime': 'Connexion WebSocket temps réel',
      'GET /v1/fine-tunes': 'Lister les fine-tunes (non implémenté)',
      'POST /v1/fine-tunes': 'Créer un fine-tune (non implémenté)',
      'GET /v1/fine-tunes/{fine_tune_id}':
        'Obtenir les détails du fine-tune (non implémenté)',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        'Annuler le fine-tune (non implémenté)',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        'Lister les événements du fine-tune (non implémenté)',
      'GET /v1/files': 'Lister les fichiers (non implémenté)',
      'POST /v1/files': 'Téléverser un fichier (non implémenté)',
      'GET /v1/files/{file_id}':
        'Obtenir les informations du fichier (non implémenté)',
      'DELETE /v1/files/{file_id}': 'Supprimer le fichier (non implémenté)',
      'GET /v1/files/{file_id}/content':
        'Obtenir le contenu du fichier (non implémenté)',
    },
  },
  ja: {
    title: 'OpenMind API リファレンス',
    description:
      'Chat、Responses、画像、ファイル、動画、埋め込み、音声、リランキング、Claude 互換 Messages、Gemini 互換モデルエンドポイントに対応した OpenAI 互換 API ゲートウェイです。',
    tags: {
      '获取模型列表': 'モデル',
      'OpenAI格式(Chat)': 'OpenAI 互換 Chat',
      'OpenAI格式(Responses)': 'OpenAI 互換 Responses',
      图片生成: '画像生成',
      '图片生成/OpenAI兼容格式': '画像生成 / OpenAI 互換',
      '图片生成/Qwen千问': '画像生成 / Qwen',
      视频生成: '動画生成',
      '视频生成/Sora兼容格式': '動画生成 / Sora 互換',
      '视频生成/Kling格式': '動画生成 / Kling',
      '视频生成/即梦格式': '動画生成 / Jimeng',
      'Claude格式(Messages)': 'Claude 互換 Messages',
      Gemini格式: 'Gemini 互換',
      'OpenAI格式(Embeddings)': 'OpenAI 互換 Embeddings',
      '文本补全(Completions)': 'テキスト補完',
      'OpenAI音频(Audio)': 'OpenAI 互換 Audio',
      '重排序(Rerank)': 'リランキング',
      Moderations: 'モデレーション',
      Realtime: 'リアルタイム',
      未实现: '未実装',
      '未实现/Fine-tunes': '未実装 / Fine-tunes',
      '未实现/Files': '未実装 / Files',
    },
    operations: {
      'GET /v1/models': 'モデル一覧を取得',
      'GET /v1beta/models': 'Gemini 形式のモデル一覧を取得',
      'POST /v1/chat/completions': 'チャット応答を作成',
      'POST /v1/responses': 'レスポンスを作成',
      'POST /v1/responses/compact': '会話を圧縮',
      'POST /v1/images/generations': '画像を生成',
      'POST /v1/images/edits': '画像を編集',
      'POST /v1/videos': '動画を作成',
      'GET /v1/videos/{task_id}': '動画タスクの状態を取得',
      'GET /v1/videos/{task_id}/content': '動画コンテンツを取得',
      'POST /kling/v1/videos/text2video': 'Kling テキストから動画',
      'GET /kling/v1/videos/text2video/{task_id}':
        'Kling テキストから動画タスクの状態を取得',
      'POST /kling/v1/videos/image2video': 'Kling 画像から動画',
      'GET /kling/v1/videos/image2video/{task_id}':
        'Kling 画像から動画タスクの状態を取得',
      'POST /jimeng/': 'Jimeng 動画生成',
      'POST /v1/video/generations': '動画生成タスクを作成',
      'GET /v1/video/generations/{task_id}': '動画生成タスクの状態を取得',
      'POST /v1/messages': 'Claude チャット',
      'POST /v1beta/models/{model}:generateContent': 'Gemini 画像生成',
      'POST /v1/engines/{model}/embeddings': 'Gemini 埋め込み',
      'POST /v1/embeddings': '埋め込みを作成',
      'POST /v1/completions': 'テキスト補完を作成',
      'POST /v1/audio/transcriptions': '音声を文字起こし',
      'POST /v1/audio/translations': '音声を翻訳',
      'POST /v1/audio/speech': '音声を作成',
      'POST /v1/rerank': 'ドキュメントをリランキング',
      'POST /v1/moderations': 'コンテンツをモデレーション',
      'GET /v1/realtime': 'リアルタイム WebSocket 接続',
      'GET /v1/fine-tunes': 'Fine-tune 一覧を取得（未実装）',
      'POST /v1/fine-tunes': 'Fine-tune を作成（未実装）',
      'GET /v1/fine-tunes/{fine_tune_id}':
        'Fine-tune 詳細を取得（未実装）',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        'Fine-tune をキャンセル（未実装）',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        'Fine-tune イベントを取得（未実装）',
      'GET /v1/files': 'ファイル一覧を取得（未実装）',
      'POST /v1/files': 'ファイルをアップロード（未実装）',
      'GET /v1/files/{file_id}': 'ファイル情報を取得（未実装）',
      'DELETE /v1/files/{file_id}': 'ファイルを削除（未実装）',
      'GET /v1/files/{file_id}/content':
        'ファイル内容を取得（未実装）',
    },
  },
  ru: {
    title: 'Справочник OpenMind API',
    description:
      'OpenAI-совместимый API-шлюз для чата, Responses, изображений, файлов, видео, эмбеддингов, аудио, rerank, Claude-совместимых Messages и Gemini-совместимых эндпоинтов моделей.',
    tags: {
      '获取模型列表': 'Модели',
      'OpenAI格式(Chat)': 'OpenAI-совместимый Chat',
      'OpenAI格式(Responses)': 'OpenAI-совместимый Responses',
      图片生成: 'Генерация изображений',
      '图片生成/OpenAI兼容格式': 'Генерация изображений / OpenAI',
      '图片生成/Qwen千问': 'Генерация изображений / Qwen',
      视频生成: 'Генерация видео',
      '视频生成/Sora兼容格式': 'Генерация видео / Sora',
      '视频生成/Kling格式': 'Генерация видео / Kling',
      '视频生成/即梦格式': 'Генерация видео / Jimeng',
      'Claude格式(Messages)': 'Claude-совместимые Messages',
      Gemini格式: 'Gemini-совместимый формат',
      'OpenAI格式(Embeddings)': 'OpenAI-совместимые Embeddings',
      '文本补全(Completions)': 'Текстовые completions',
      'OpenAI音频(Audio)': 'OpenAI-совместимое Audio',
      '重排序(Rerank)': 'Rerank',
      Moderations: 'Модерация',
      Realtime: 'Realtime',
      未实现: 'Не реализовано',
      '未实现/Fine-tunes': 'Не реализовано / Fine-tunes',
      '未实现/Files': 'Не реализовано / Files',
    },
    operations: {
      'GET /v1/models': 'Получить список моделей',
      'GET /v1beta/models': 'Получить модели в формате Gemini',
      'POST /v1/chat/completions': 'Создать чат-ответ',
      'POST /v1/responses': 'Создать response',
      'POST /v1/responses/compact': 'Сжать диалог',
      'POST /v1/images/generations': 'Сгенерировать изображение',
      'POST /v1/images/edits': 'Отредактировать изображение',
      'POST /v1/videos': 'Создать видео',
      'GET /v1/videos/{task_id}': 'Получить статус видео-задачи',
      'GET /v1/videos/{task_id}/content': 'Получить видео-контент',
      'POST /kling/v1/videos/text2video': 'Kling: текст в видео',
      'GET /kling/v1/videos/text2video/{task_id}':
        'Получить статус Kling текст-в-видео',
      'POST /kling/v1/videos/image2video': 'Kling: изображение в видео',
      'GET /kling/v1/videos/image2video/{task_id}':
        'Получить статус Kling изображение-в-видео',
      'POST /jimeng/': 'Генерация видео Jimeng',
      'POST /v1/video/generations': 'Создать задачу генерации видео',
      'GET /v1/video/generations/{task_id}':
        'Получить статус задачи генерации видео',
      'POST /v1/messages': 'Чат Claude',
      'POST /v1beta/models/{model}:generateContent':
        'Генерация изображений Gemini',
      'POST /v1/engines/{model}/embeddings': 'Embeddings Gemini',
      'POST /v1/embeddings': 'Создать embeddings',
      'POST /v1/completions': 'Создать текстовое completion',
      'POST /v1/audio/transcriptions': 'Транскрибировать аудио',
      'POST /v1/audio/translations': 'Перевести аудио',
      'POST /v1/audio/speech': 'Создать речь',
      'POST /v1/rerank': 'Переупорядочить документы',
      'POST /v1/moderations': 'Модерировать контент',
      'GET /v1/realtime': 'Realtime WebSocket подключение',
      'GET /v1/fine-tunes': 'Список fine-tunes (не реализовано)',
      'POST /v1/fine-tunes': 'Создать fine-tune (не реализовано)',
      'GET /v1/fine-tunes/{fine_tune_id}':
        'Получить детали fine-tune (не реализовано)',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        'Отменить fine-tune (не реализовано)',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        'Получить события fine-tune (не реализовано)',
      'GET /v1/files': 'Список файлов (не реализовано)',
      'POST /v1/files': 'Загрузить файл (не реализовано)',
      'GET /v1/files/{file_id}': 'Получить информацию о файле (не реализовано)',
      'DELETE /v1/files/{file_id}': 'Удалить файл (не реализовано)',
      'GET /v1/files/{file_id}/content':
        'Получить содержимое файла (не реализовано)',
    },
  },
  vi: {
    title: 'Tài liệu OpenMind API',
    description:
      'Cổng API tương thích OpenAI cho chat, Responses, hình ảnh, tệp, video, embeddings, âm thanh, rerank, Messages tương thích Claude và endpoint mô hình tương thích Gemini.',
    tags: {
      '获取模型列表': 'Mô hình',
      'OpenAI格式(Chat)': 'Chat tương thích OpenAI',
      'OpenAI格式(Responses)': 'Responses tương thích OpenAI',
      图片生成: 'Tạo hình ảnh',
      '图片生成/OpenAI兼容格式': 'Tạo hình ảnh / tương thích OpenAI',
      '图片生成/Qwen千问': 'Tạo hình ảnh / Qwen',
      视频生成: 'Tạo video',
      '视频生成/Sora兼容格式': 'Tạo video / tương thích Sora',
      '视频生成/Kling格式': 'Tạo video / Kling',
      '视频生成/即梦格式': 'Tạo video / Jimeng',
      'Claude格式(Messages)': 'Messages tương thích Claude',
      Gemini格式: 'Tương thích Gemini',
      'OpenAI格式(Embeddings)': 'Embeddings tương thích OpenAI',
      '文本补全(Completions)': 'Hoàn thành văn bản',
      'OpenAI音频(Audio)': 'Audio tương thích OpenAI',
      '重排序(Rerank)': 'Rerank',
      Moderations: 'Kiểm duyệt',
      Realtime: 'Thời gian thực',
      未实现: 'Chưa triển khai',
      '未实现/Fine-tunes': 'Chưa triển khai / Fine-tunes',
      '未实现/Files': 'Chưa triển khai / Files',
    },
    operations: {
      'GET /v1/models': 'Lấy danh sách mô hình',
      'GET /v1beta/models': 'Lấy danh sách mô hình định dạng Gemini',
      'POST /v1/chat/completions': 'Tạo phản hồi chat',
      'POST /v1/responses': 'Tạo response',
      'POST /v1/responses/compact': 'Nén hội thoại',
      'POST /v1/images/generations': 'Tạo hình ảnh',
      'POST /v1/images/edits': 'Chỉnh sửa hình ảnh',
      'POST /v1/videos': 'Tạo video',
      'GET /v1/videos/{task_id}': 'Lấy trạng thái tác vụ video',
      'GET /v1/videos/{task_id}/content': 'Lấy nội dung video',
      'POST /kling/v1/videos/text2video': 'Kling văn bản thành video',
      'GET /kling/v1/videos/text2video/{task_id}':
        'Lấy trạng thái Kling văn bản thành video',
      'POST /kling/v1/videos/image2video': 'Kling hình ảnh thành video',
      'GET /kling/v1/videos/image2video/{task_id}':
        'Lấy trạng thái Kling hình ảnh thành video',
      'POST /jimeng/': 'Tạo video Jimeng',
      'POST /v1/video/generations': 'Tạo tác vụ tạo video',
      'GET /v1/video/generations/{task_id}':
        'Lấy trạng thái tác vụ tạo video',
      'POST /v1/messages': 'Chat Claude',
      'POST /v1beta/models/{model}:generateContent': 'Tạo hình ảnh Gemini',
      'POST /v1/engines/{model}/embeddings': 'Embeddings Gemini',
      'POST /v1/embeddings': 'Tạo embeddings',
      'POST /v1/completions': 'Tạo hoàn thành văn bản',
      'POST /v1/audio/transcriptions': 'Chuyển giọng nói thành văn bản',
      'POST /v1/audio/translations': 'Dịch âm thanh',
      'POST /v1/audio/speech': 'Tạo giọng nói',
      'POST /v1/rerank': 'Xếp hạng lại tài liệu',
      'POST /v1/moderations': 'Kiểm duyệt nội dung',
      'GET /v1/realtime': 'Kết nối WebSocket thời gian thực',
      'GET /v1/fine-tunes': 'Liệt kê fine-tunes (chưa triển khai)',
      'POST /v1/fine-tunes': 'Tạo fine-tune (chưa triển khai)',
      'GET /v1/fine-tunes/{fine_tune_id}':
        'Lấy chi tiết fine-tune (chưa triển khai)',
      'POST /v1/fine-tunes/{fine_tune_id}/cancel':
        'Hủy fine-tune (chưa triển khai)',
      'GET /v1/fine-tunes/{fine_tune_id}/events':
        'Lấy sự kiện fine-tune (chưa triển khai)',
      'GET /v1/files': 'Liệt kê tệp (chưa triển khai)',
      'POST /v1/files': 'Tải tệp lên (chưa triển khai)',
      'GET /v1/files/{file_id}': 'Lấy thông tin tệp (chưa triển khai)',
      'DELETE /v1/files/{file_id}': 'Xóa tệp (chưa triển khai)',
      'GET /v1/files/{file_id}/content':
        'Lấy nội dung tệp (chưa triển khai)',
    },
  },
}

const localeFallback: Record<DocsLocale, DocsLocale> = {
  en: 'en',
  zh: 'zh',
  fr: 'en',
  ja: 'en',
  ru: 'en',
  vi: 'en',
}

const hiddenPathPrefixes = ['/v1/fine-tunes']
const hiddenOperationKeys = new Set(['GET /v1/files'])
const fileOperationKeys = new Set([
  'POST /v1/files',
  'GET /v1/files/{file_id}',
  'DELETE /v1/files/{file_id}',
  'GET /v1/files/{file_id}/content',
])
const fileTagNames: Record<DocsLocale, string> = {
  zh: '文件',
  en: 'Files',
  fr: 'Files',
  ja: 'Files',
  ru: 'Files',
  vi: 'Files',
}

function getDocsLocale(language: string): DocsLocale {
  const normalized = language.toLowerCase()
  if (normalized.startsWith('zh')) return 'zh'
  if (normalized.startsWith('fr')) return 'fr'
  if (normalized.startsWith('ja')) return 'ja'
  if (normalized.startsWith('ru')) return 'ru'
  if (normalized.startsWith('vi')) return 'vi'
  return 'en'
}

function localizeSpec(locale: DocsLocale) {
  const fallback = docsLocales[localeFallback[locale]]
  const localized = docsLocales[locale]
  const merged: LocalizedDocs = {
    title: localized.title,
    description: localized.description,
    tags: { ...fallback.tags, ...localized.tags },
    operations: { ...fallback.operations, ...localized.operations },
  }

  const spec = structuredClone(relaySpec) as typeof relaySpec

  spec.info = {
    ...spec.info,
    title: merged.title,
    description: merged.description,
  }

  spec.tags = spec.tags
    ?.filter((tag) => !tag.name.includes('鏈疄鐜'))
    .map((tag) => ({
      ...tag,
      name: merged.tags[tag.name] ?? tag.name,
    }))

  for (const [path, pathItem] of Object.entries(spec.paths)) {
    if (hiddenPathPrefixes.some((prefix) => path.startsWith(prefix))) {
      delete spec.paths[path as keyof typeof spec.paths]
      continue
    }

    const mutablePathItem = pathItem as Record<string, unknown>
    for (const [method, operation] of Object.entries(pathItem)) {
      const operationKey = `${method.toUpperCase()} ${path}`
      if (hiddenOperationKeys.has(operationKey)) {
        delete mutablePathItem[method]
        continue
      }
      if (!operation || typeof operation !== 'object') continue

      if ('summary' in operation && merged.operations[operationKey]) {
        operation.summary = merged.operations[operationKey]
      }
      if ('tags' in operation && Array.isArray(operation.tags)) {
        operation.tags = fileOperationKeys.has(operationKey)
          ? [fileTagNames[locale]]
          : operation.tags.map((tag: string) => merged.tags[tag] ?? tag)
      }
    }

    if (Object.keys(pathItem).length === 0) {
      delete spec.paths[path as keyof typeof spec.paths]
    }
  }

  return {
    ...spec,
    servers: [
      {
        url: API_BASE_URL,
        description: 'OpenMind API',
      },
    ],
  }
}

export function ApiDocs() {
  const { i18n } = useTranslation()
  const locale = getDocsLocale(i18n.resolvedLanguage || i18n.language || 'en')
  const scalarSpec = useMemo(() => localizeSpec(locale), [locale])

  const configuration: Partial<ApiReferenceConfiguration> = useMemo(
    () => ({
      title: scalarSpec.info.title,
      content: scalarSpec,
      servers: scalarSpec.servers,
      baseServerURL: API_BASE_URL,
      layout: 'modern',
      theme: 'default',
      showDeveloperTools: 'never',
      agent: {
        disabled: true,
      },
      hideClientButton: true,
      hideDarkModeToggle: true,
      hideModels: true,
      defaultHttpClient: {
        targetKey: 'shell',
        clientKey: 'curl',
      },
      authentication: {
        preferredSecurityScheme: 'BearerAuth',
      },
      persistAuth: true,
      hideTestRequestButton: true,
      showOperationId: false,
      documentDownloadType: 'none',
      operationTitleSource: 'summary',
      defaultOpenFirstTag: true,
      orderSchemaPropertiesBy: 'preserve',
      telemetry: false,
    }),
    [scalarSpec]
  )

  return (
    <PublicLayout showMainContainer={false}>
      <main className='scalar-docs-page'>
        <ApiReferenceReact
          key={locale}
          configuration={configuration}
        />
      </main>
    </PublicLayout>
  )
}
