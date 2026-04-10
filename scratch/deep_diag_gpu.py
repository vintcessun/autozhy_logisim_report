import onnxruntime as ort
import os

def check_gpu():
    print("=== ONNX Runtime GPU 深度扫描 ===")
    providers = ort.get_available_providers()
    print(f"系统可见供应商: {providers}")
    
    # 检查 CUDA 路径
    cuda_path = os.environ.get("CUDA_PATH", "未设置")
    print(f"CUDA_PATH 环境变量: {cuda_path}")

    # 尝试加载
    if "CUDAExecutionProvider" not in providers:
        print("\n[!] 警告: CUDAExecutionProvider 在可用列表中缺失。")
        print("这通常意味着:")
        print("1. 未安装 Zlib 库 (Windows 下需要 zlibwapi.dll)")
        print("2. cuDNN DLL 路径未加入系统 PATH")
        print("3. onnxruntime-gpu 版本与 CUDA 11.8 不完全匹配")
    else:
        try:
            # 尝试初始化一个最小会话
            # 注意: 这里使用 None 会报错，但能触发 Provider 加载逻辑
            ort.InferenceSession(b"", providers=['CUDAExecutionProvider'])
            print("\n[+] CUDA 加载测试成功!")
        except Exception as e:
            print(f"\n[-] CUDA 加载测试失败: {e}")

if __name__ == "__main__":
    check_gpu()
