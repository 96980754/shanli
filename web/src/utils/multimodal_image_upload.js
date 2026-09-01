import { message } from 'ant-design-vue'
import { multimodalApi } from '@/apis/agent_api'
import { i18n } from '@/i18n'

const MAX_IMAGE_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024

export const uploadMultimodalImage = async (file) => {
  if (!file) return null

  if (file.size > MAX_IMAGE_UPLOAD_SIZE_BYTES) {
    message.error(i18n.global.t('fileUtil.imageTooLarge'))
    return null
  }

  if (!file.type?.startsWith('image/')) {
    message.error(i18n.global.t('fileUtil.selectValidImage'))
    return null
  }

  try {
    message.loading({ content: i18n.global.t('fileUtil.processingImage'), key: 'image-upload' })

    const result = await multimodalApi.uploadImage(file)
    if (!result.success) {
      message.error({
        content: i18n.global.t('fileUtil.imageProcessFailed', { error: result.error }),
        key: 'image-upload'
      })
      return null
    }

    message.success({
      content: i18n.global.t('fileUtil.imageProcessSuccess'),
      key: 'image-upload',
      duration: 2
    })

    return {
      success: true,
      imageContent: result.image_content,
      thumbnailContent: result.thumbnail_content,
      width: result.width,
      height: result.height,
      format: result.format,
      mimeType: result.mime_type || file.type,
      sizeBytes: result.size_bytes,
      originalName: file.name || result.original_filename || 'pasted-image'
    }
  } catch (error) {
    console.error('图片上传失败:', error) // i18n-ignore
    message.error({
      content: i18n.global.t('fileUtil.imageUploadFailed', {
        message: error.message || i18n.global.t('fileUtil.unknownError')
      }),
      key: 'image-upload'
    })
    return null
  }
}
