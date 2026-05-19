import glob
import os

for filepath in glob.glob("include/*.h"):
    with open(filepath, "r") as f:
        lines = f.readlines()
    
    if len(lines) == 0: continue
    
    # Check if already wrapped
    content = "".join(lines)
    if '#ifdef __cplusplus' in content:
        continue
    
    out_lines = []
    # Find the include guard #define
    guard_define_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("#define"):
            guard_define_idx = i
            break
            
    # Find the last #endif
    last_endif_idx = -1
    for i in range(len(lines)-1, -1, -1):
        if lines[i].startswith("#endif"):
            last_endif_idx = i
            break
            
    if guard_define_idx != -1 and last_endif_idx != -1:
        out_lines = lines[:guard_define_idx+1]
        out_lines.append("\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n")
        out_lines.extend(lines[guard_define_idx+1:last_endif_idx])
        out_lines.append("\n#ifdef __cplusplus\n}\n#endif\n\n")
        out_lines.extend(lines[last_endif_idx:])
        
        with open(filepath, "w") as f:
            f.writelines(out_lines)
        print(f"Fixed {filepath}")
    else:
        print(f"Skipped {filepath}")
