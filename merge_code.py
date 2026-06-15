import os

def merge_files_for_llm(output_filename="merged_code_for_prompt.txt"):
    # 스크립트가 실행되는 위치가 아닌, 스크립트 파일이 존재하는 폴더를 절대 경로로 기준 잡음
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(base_dir, output_filename)
    
    target_extensions = ('.py', '.md', '.yaml', '.json')
    skip_dirs = {'.venv', '__pycache__', '.git', 'logs'}
    
    merged_count = 0
    
    with open(output_path, 'w', encoding='utf-8') as outfile:
        # base_dir 기준으로 탐색 시작
        for root, dirs, files in os.walk(base_dir):
            # 무시할 디렉토리 필터링
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            
            for file in files:
                if file.endswith(target_extensions):
                    filepath = os.path.join(root, file)
                    
                    # 자기 자신(출력 파일)은 읽지 않도록 방어
                    if filepath == output_path:
                        continue
                    
                    # 결과물에 표시될 경로는 전체 절대 경로 대신 상대 경로 느낌으로 정리
                    display_path = filepath.replace(base_dir + os.sep, "")
                    
                    outfile.write(f"\n{'='*60}\n")
                    outfile.write(f"FILE: {display_path}\n")
                    outfile.write(f"{'='*60}\n\n")
                    
                    try:
                        # 1차 시도: utf-8 인코딩으로 읽기
                        with open(filepath, 'r', encoding='utf-8') as infile:
                            outfile.write(infile.read())
                        merged_count += 1
                    except UnicodeDecodeError:
                        # 2차 시도: utf-8 실패 시 Windows 기본 인코딩(cp949)으로 읽기
                        try:
                            with open(filepath, 'r', encoding='cp949') as infile:
                                outfile.write(infile.read())
                            merged_count += 1
                        except Exception as e:
                            outfile.write(f"Error reading file (encoding): {e}\n")
                    except Exception as e:
                        outfile.write(f"Error reading file: {e}\n")
                        
    return merged_count, output_path

if __name__ == "__main__":
    count, final_path = merge_files_for_llm()
    
    if count > 0:
        print(f"성공: 총 {count}개의 파일을 병합했습니다.")
        print(f"저장 위치: {final_path}")
    else:
        print("경고: 병합할 파일을 하나도 찾지 못했습니다. 확장자나 경로를 확인해주세요.")