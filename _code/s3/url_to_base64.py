import os
import hashlib
import subprocess
import base64
import mimetypes
from PIL import Image, UnidentifiedImageError # 导入PIL库
import io # 用于将bytes包装成file-like对象

# --- 配置 ---
TEMP_PATH = 'data/tmp_files' # 缓存目录

# 确保缓存目录存在
os.makedirs(TEMP_PATH, exist_ok=True)

# PIL 格式到 MIME 类型的映射 (可以根据需要扩展)
PIL_FORMAT_TO_MIME = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
    'BMP': 'image/bmp',
    'TIFF': 'image/tiff',
}

# MIME 类型到 文件扩展名的映射 (mimetypes通常能处理好，但可以自定义)
MIME_TO_EXTENSION = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/tiff': '.tif', # 或 .tiff
}

def get_image_base64(url, target_dir=TEMP_PATH):
    """
    下载图片（使用缓存）并转换为data URI格式，使用PIL判断类型。

    Args:
        url (str): 图片URL
        target_dir (str): 缓存文件存放目录

    Returns:
        str or None: 完整的data URI格式字符串 "data:{mime_type};base64,{base64_data}"
                     或在失败时返回 None
    """
    print(f"🔄 处理 URL: {url}")
    binary_data = None
    cached_file_path = None
    mime_type = None

    try:
        # 1. --- 检查缓存 ---
        url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
        # 在缓存中寻找可能存在的文件（基于哈希，不限扩展名）
        existing_files = [f for f in os.listdir(target_dir)
                          if os.path.isfile(os.path.join(target_dir, f)) and
                          os.path.splitext(os.path.basename(f))[0] == url_hash]

        if existing_files:
            cached_file_path = os.path.join(target_dir, existing_files[0])
            print(f"✅ 缓存命中: {cached_file_path}")
            try:
                with open(cached_file_path, 'rb') as f:
                    binary_data = f.read()
                # 从缓存文件名或内容推断MIME类型 (优先用PIL)
                try:
                    img = Image.open(io.BytesIO(binary_data))
                    pil_format = img.format
                    img.close() # 及时关闭
                    mime_type = PIL_FORMAT_TO_MIME.get(pil_format)
                    if not mime_type:
                         # 尝试从文件名扩展名推断 (作为备选)
                        mime_type, _ = mimetypes.guess_type(cached_file_path)
                        print(f"⚠️ PIL 未知格式 '{pil_format}', 从扩展名推断为: {mime_type}")
                    else:
                         print(f"✅ PIL 从缓存内容识别格式: {pil_format} -> {mime_type}")

                except UnidentifiedImageError:
                    print(f"⚠️ 缓存文件 '{cached_file_path}' 不是有效图片格式 (PIL无法识别)。将尝试重新下载。")
                    binary_data = None # 清除缓存数据，强制重新下载
                    try:
                        os.remove(cached_file_path) # 删除无效缓存
                        print(f"🗑️ 已删除无效缓存文件: {cached_file_path}")
                    except OSError as e:
                        print(f"❌ 删除无效缓存文件失败: {e}")
                except Exception as e_pil:
                    print(f"⚠️ 处理缓存文件时发生PIL错误: {e_pil}。将尝试重新下载。")
                    binary_data = None # 清除缓存数据

            except IOError as e:
                print(f"❌ 读取缓存文件失败: {e}")
                binary_data = None # 读取失败，强制重新下载

        # 2. --- 下载 (如果缓存未命中或无效) ---
        if binary_data is None:
            print(f"⬇️ 缓存未命中或无效，尝试下载: {url}")
            curl_command = [
                'curl',
                '-k',          # 忽略SSL证书验证
                '-L',          # 跟随重定向
                '-s',          # 静默模式
                '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0',
                 # '-v', # 可选：启用详细输出进行调试
                '--max-time', '15', # 设置超时时间，例如15秒
                url
            ]

            result = subprocess.run(curl_command, capture_output=True)

            if result.returncode == 0 and result.stdout:
                binary_data = result.stdout
                print(f"✅ 下载成功 (大小: {len(binary_data)} bytes)")

                # 3. --- 使用 PIL 推断类型 (下载后) ---
                try:
                    # 使用 io.BytesIO 将 bytes 数据包装成文件流供 PIL 读取
                    img = Image.open(io.BytesIO(binary_data))
                    pil_format = img.format # 获取 PIL 识别的格式 (e.g., 'JPEG', 'PNG')
                    img.close() # 操作完成后关闭图片对象

                    mime_type = PIL_FORMAT_TO_MIME.get(pil_format)

                    if not mime_type:
                        print(f"⚠️ PIL 识别出格式 '{pil_format}' 但未在映射中找到对应 MIME 类型。")
                         # 可以尝试 mimetypes 作为后备
                        guessed_mime, _ = mimetypes.guess_type(url)
                        if guessed_mime and 'image' in guessed_mime:
                             mime_type = guessed_mime
                             print(f"   (后备) Mimetypes 根据 URL 推断为: {mime_type}")
                        else:
                             mime_type = 'application/octet-stream' # 最终后备
                             print(f"   (最终后备) 无法确定 MIME 类型，设为: {mime_type}")
                    else:
                        print(f"✅ PIL 识别格式: {pil_format} -> {mime_type}")

                    # 检查是否实际上是 JSON 或其他非图片内容被错误识别
                    # （PIL 通常会在此之前的 open() 步骤抛出 UnidentifiedImageError）
                    # 但可以加一层保险，例如检查常见的错误JSON结构
                    try:
                        decoded_str = binary_data.decode('utf-8', errors='ignore')
                        if decoded_str.strip().startswith('{') and decoded_str.strip().endswith('}'):
                           if 'error' in decoded_str.lower() or 'message' in decoded_str.lower():
                               print(f"⚠️ 检测到疑似 JSON 错误信息，即使 PIL 可能识别了格式。内容: {decoded_str[:200]}...")
                               raise ValueError("疑似下载到JSON错误信息")
                    except UnicodeDecodeError:
                        pass # 解码失败，很可能是二进制图片，是正常的

                    # 4. --- 写入缓存 (下载成功且是有效图片后) ---
                    file_ext = MIME_TO_EXTENSION.get(mime_type, mimetypes.guess_extension(mime_type) or '.bin') # 获取扩展名
                    save_filename = f"{url_hash}{file_ext}"
                    save_path = os.path.join(target_dir, save_filename)
                    try:
                        with open(save_path, 'wb') as f:
                            f.write(binary_data)
                        print(f"💾 已缓存图片到: {save_path}")
                    except IOError as e:
                        print(f"❌ 写入缓存文件失败: {e}") # 非致命错误，继续处理

                except UnidentifiedImageError:
                    # 这通常意味着下载到的不是 PIL 能识别的图片格式 (可能是 HTML, JSON 错误等)
                    print(f"❌ 下载的内容不是有效的图片格式 (PIL无法识别)。")
                    try:
                        # 尝试解码为文本以显示可能是什么内容
                        error_content = binary_data.decode('utf-8', errors='replace')
                        print(f"   下载到的内容前200字符: {error_content[:200]}...")
                        # 特别检查是否像 JSON
                        if error_content.strip().startswith('{') and error_content.strip().endswith('}'):
                             print("   内容看起来像 JSON。")
                    except Exception as decode_err:
                         print(f"   无法将下载内容解码为文本: {decode_err}")
                    return None # 明确返回 None 表示失败
                except ValueError as ve: # 捕获上面我们自己抛出的 ValueError
                     print(f"❌ 处理失败: {ve}")
                     return None
                except Exception as e_pil:
                     print(f"❌ 使用 PIL 处理图片时发生错误: {str(e_pil)}")
                     return None

            else:
                # 下载失败 (curl 返回非 0 或无输出)
                print(f"❌ 下载失败 (curl 返回码: {result.returncode})")
                if result.stderr:
                    try:
                        stderr_output = result.stderr.decode('utf-8', errors='replace')
                        print(f"   错误信息 (curl stderr): {stderr_output.strip()}")
                    except Exception as decode_err:
                         print(f"   无法解码 curl 的 stderr: {decode_err}")
                         print(f"   原始 stderr bytes: {result.stderr}")
                # 尝试从 result.stdout 获取可能的错误信息 (有时错误页面会输出到 stdout)
                if result.stdout:
                     try:
                         stdout_output = result.stdout.decode('utf-8', errors='replace')
                         if len(stdout_output) < 500: # 只显示较短的输出，避免大量HTML页面
                             print(f"   可能的错误页面内容 (curl stdout): {stdout_output.strip()}")
                         else:
                             print(f"   curl stdout 内容过长 ({len(result.stdout)} bytes)，可能为错误页面，已省略。")
                     except Exception:
                         print("   无法解码 curl 的 stdout。")

                return None # 下载失败

        # 5. --- 转换 Base64 并构建 Data URI (如果数据有效且类型已知) ---
        if binary_data and mime_type and 'image' in mime_type: # 确保是图片类型
            base64_data = base64.b64encode(binary_data).decode('utf-8')
            data_uri = f"data:{mime_type};base64,{base64_data}"

            print("✅ 成功转换为 Data URI！")
            print(f"📊 Data URI 长度: {len(data_uri)} 字符")
            print(f"🎯 MIME类型: {mime_type}")

            return data_uri
        elif binary_data and mime_type:
             print(f"❌ 获取到的MIME类型 '{mime_type}' 不是 'image/*'，无法转换为图片Data URI。")
             return None
        elif binary_data and not mime_type:
             print(f"❌ 无法确定下载内容的MIME类型。")
             return None
        else:
             # binary_data 为 None 的情况已在前面处理并返回 None
             pass

    except subprocess.CalledProcessError as e:
        print(f"❌ 执行 curl 命令失败: {e}")
        if e.stderr:
            print(f"   错误输出: {e.stderr.decode(errors='replace')}")
        return None
    except FileNotFoundError:
         print("❌ 错误：找不到 'curl' 命令。请确保 curl 已安装并在系统 PATH 中。")
         return None
    except Exception as e:
        print(f"❌ 发生未预料的错误: {str(e)}")
        import traceback
        traceback.print_exc() # 打印详细的堆栈跟踪信息
        return None

# --- 示例用法 ---
if __name__ == "__main__":
    # 示例1: 一个有效的图片 URL
    image_url_ok = "https://gchat.qpic.cn/gchatpic_new/0/0-0-5EF97D26F8726DCBC95B35019D5F9C8B/0"
    data_uri_ok = get_image_base64(image_url_ok)
    if data_uri_ok:
        print("\n--- 结果 (成功) ---")
        # print(data_uri_ok[:100] + "...") # 只打印前100个字符
    else:
        print("\n--- 结果 (失败) ---")

    print("\n" + "="*30 + "\n") # 分隔符

    # 示例2: 一个可能返回 JSON 的 URL (例如 API 端点或无效图片链接)
    # 注意：这个URL只是示例，实际可能返回HTML页面或重定向
    image_url_json_like = "https://httpbin.org/get" # 这个会返回JSON
    data_uri_json = get_image_base64(image_url_json_like)
    if data_uri_json:
        print("\n--- 结果 (JSON - 预期失败) ---")
        # print(data_uri_json[:100] + "...")
    else:
        print("\n--- 结果 (JSON - 预期失败) ---")
        print("未能生成 Data URI (这是预期的，因为URL返回JSON)")

    print("\n" + "="*30 + "\n")

    # 示例3: 再次请求第一个 URL，测试缓存
    print("--- 再次请求第一个 URL (测试缓存) ---")
    data_uri_ok_cached = get_image_base64(image_url_ok)
    if data_uri_ok_cached:
        print("\n--- 结果 (缓存成功) ---")
        # print(data_uri_ok_cached[:100] + "...")
    else:
        print("\n--- 结果 (缓存失败?) ---") # 不应该发生，除非第一次失败了

    print("\n" + "="*30 + "\n")

    # 示例4: 一个无效或超时的 URL
    image_url_invalid = "https://invalid.domain.that.does.not.exist/image.jpg"
    data_uri_invalid = get_image_base64(image_url_invalid)
    if data_uri_invalid:
         print("\n--- 结果 (无效URL - 预期失败) ---")
    else:
         print("\n--- 结果 (无效URL - 预期失败) ---")
         print("未能生成 Data URI (这是预期的，因为URL无效)")