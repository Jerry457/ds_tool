import math, re, os
from copy import deepcopy

from ds_file.anim_bank import AnimBank
from ds_file.anim_build import AnimBuild
from klei.matrix3 import Matrix3, create_trans_rot_scale_pivot_matrix
from xml.etree.ElementTree import Element, ElementTree, indent

def lerp(a, b, l):
    return a + (b - a) * l

def lerp_angle(start_angle: float, end_angle: float, blend: float, spin: int):
    if abs(end_angle - start_angle) > 180:
        if end_angle < start_angle:
            end_angle += 360
        else:
            end_angle -= 360

    result = lerp(start_angle, end_angle, blend)
    return math.radians(result)

def get_key_blend(keys: list[Element], anim_length: int, frame_num: int, looping: bool) -> tuple[Element, Element, float]:
    key_count = len(keys)
    start_key = keys[0]
    end_key = keys[0]
    for key in keys:
        time = int(key.get("time", 0))
        if time <= frame_num:
            start_key = key
            end_key = key
            if time == frame_num:
                break
        else:
            end_key = key
            break

    start_time = int(start_key.get("time", 0))
    end_time = int(end_key.get("time", 0))

    if start_key == keys[key_count - 1] and looping and start_time != frame_num:
        end_key = keys[0]
        end_time = anim_length

    blend = 0.0
    if end_time != start_time:
        blend = (frame_num - start_time) / (end_time - start_time)

    return start_key, end_key, start_time, end_time, blend

def flatten(bone_frame: dict, bone_frames: list[dict]):
    if bone_frame["is_flattened"] or bone_frame["parent_id"] == -1:
        return

    for parent in bone_frames:
        if parent["id"] == bone_frame["parent_id"]:
            flatten(parent, bone_frames)

            bone_frame["scale_x"] *= parent["scale_x"]
            bone_frame["scale_y"] *= parent["scale_y"]

            sx = bone_frame["x"] * parent["scale_x"]
            sy = bone_frame["y"] * parent["scale_y"]
            s = math.sin(parent["angle"])
            c = math.cos(parent["angle"])

            bone_frame["x"] = parent["x"] + sx * c - sy * s
            bone_frame["y"] = parent["y"] + sx * s + sy * c
            bone_frame["angle"] += parent["angle"]

            break

    bone_frame["is_flattened"] = True

def extend_bounding_box(symbol: Element, symbol_name: str, first: bool, rect: dict, frame_num: int, matrix3: Matrix3):
    frame = symbol.find(f"file[@id='{str(frame_num)}\']")

    if frame is None:
        print(f"WARNING: frame {frame_num} of animation symbol {symbol_name} is being used by the animation but not defined by the build.")
        return False

    xs = [0.0] * 4
    ys = [0.0] * 4

    w = int(frame.get("width"))
    h = int(frame.get("height"))
    x = w / 2 - float(frame.get("pivot_x", "0")) * w
    y = h / 2 - float(frame.get("pivot_y", "0")) * h
    y = -y

    m = matrix3.matrix
    xs[0] = m[0][2] - w / 2.0 + float(x)
    xs[1] = xs[0] + m[0][0] * w
    xs[2] = xs[0] + m[0][1] * h
    xs[3] = xs[0] + m[0][0] * w + m[0][1] * h

    ys[0] = m[1][2] - h / 2.0 + float(y)
    ys[1] = ys[0] + m[1][0] * w
    ys[2] = ys[0] + m[1][1] * h
    ys[3] = ys[0] + m[1][0] * w + m[1][1] * h

    x1 = xs[0]
    x2 = xs[0]
    y1 = ys[0]
    y2 = ys[0]

    for i in range(1, 4):
        x1 = min(x1, xs[i])
        x2 = max(x2, xs[i])
        y1 = min(y1, ys[i])
        y2 = max(y2, ys[i])

    if not first and x1 > rect["x1"] - rect["x2"] / 2.0:
        x1 = rect["x1"] - rect["x2"] / 2.0
    if not first and x2 < rect["x1"] + rect["x2"] / 2.0:
        x2 = rect["x1"] + rect["x2"] / 2.0
    if not first and y1 > rect["y1"] - rect["y2"] / 2.0:
        y1 = rect["y1"] - rect["y2"] / 2.0
    if not first and y2 < rect["y1"] + rect["y2"] / 2.0:
        y2 = rect["y1"] + rect["y2"] / 2.0

    w = x2 - x1
    h = y2 - y1

    # Note: rect's x1 and y2 are actually the center of the frame
    rect["x1"] = x1 + w / 2.0
    rect["y1"] = y1 + h / 2.0
    rect["x2"] = w
    rect["y2"] = h

    return True

class Scml(ElementTree):
    def __init__(self, file=None):
        if file is None:
            ElementTree.__init__(self, Element("spriter_data", scml_version="1.0", generator="BrashMonkey Spriter", generator_version="b5"))
        else:
            self.path = file
            ElementTree.__init__(self, file=file)

    def writr(self, output):
        indent(self, space="    ")
        ElementTree.write(self, output, encoding="utf-8")

    def build_image(self, scale: float=1) -> tuple[dict, dict]:
        build_path = os.path.split(self.path)[0]
        build_name = os.path.splitext(os.path.basename(self.path))[0]

        build_data = {"type": "Build", "version": AnimBuild.version, "name": build_name, "scale": scale, "Symbol": {}}
        symbols_images = {}

        folders = self.findall("folder")
        for folder in folders:
            print("build symbol: " + (folder_name := folder.get("name").lower()))

            files = [file for file in folder.findall("file") if file.get("name").find("(missing)") == -1]
            if files:
                build_data["Symbol"][folder_name] = []
            for file in files:
                if (match := re.search(r"(duration\'(.+?)\')", file.get("name"))) is not None:
                    duration = int(re.findall(r'\d+', match.group(1))[0])
                    for frame in build_data["Symbol"][folder_name]:
                        if frame["framenum"] == duration:
                            frame["duration"] += 1
                            break
                    continue

                w, h = int(file.get("width")), int(file.get("height"))
                framenum = int(file.get("id", "0"))

                x = w / 2 - float(file.get("pivot_x", "0")) * w
                y = h / 2 - float(file.get("pivot_y", "0")) * h
                y = -y
                build_data["Symbol"][folder_name].append({"framenum": framenum, "duration": 1, "x": x, "y": y, "w": w, "h": h})
                symbols_images[f"{folder_name}-{framenum}"] = os.path.join(build_path, file.get("name"))

        return build_data, symbols_images

    def build_scml(self, output, scale: float=1):
        scml_root = deepcopy(self.getroot())

        anim_data = {"type": "Anim", "version": 4, "banks": {}}
        build_data, symbols_images = self.build_image(scale)

        frame_duration = 1000 // AnimBank.frame_rate

        for entity in scml_root.findall("entity"):
            anim_data["banks"][entity.get("name")] = {}

            for animation in entity.findall("animation"):
                timelines = animation.findall("timeline")
                if not timelines:
                    continue
                valid_timelines = [timeline for timeline in timelines if timeline.get("object_type") != "bone"]
                bone_timelines = [timeline for timeline in timelines if timeline.get("object_type") == "bone"]
                if (valid_timelines_cont := len(valid_timelines)) == 0:
                    continue

                anim_length = int(animation.get("length", 0))
                looping = animation.get("looping", "true") == "true"
                mainline = animation.find("mainline")
                time_max_key = mainline.findall("key")[-1]

                print("convert animation: " + (animation_name := animation.get("name")))
                anim_data["banks"][entity.get("name")][animation_name] = {"framerate": AnimBank.frame_rate, "numframes": anim_length//(frame_duration), "frames": []}
                for timeline in valid_timelines:
                    keys = timeline.findall("key")

                    timeline_id = timeline.get("id", "0")

                    timeline_mainline_object_ref = mainline.find(f".//object_ref[@timeline='{timeline_id}'][@key='{keys[0].get('id', '0')}']")
                    mainline_timeline_key_object = animation.find(f"timeline[@id='{timeline_mainline_object_ref.get('timeline', '0')}']/key[@id='{timeline_mainline_object_ref.get('key', '0')}']/object")
                    mainline_file = scml_root.find(f"folder[@id='{mainline_timeline_key_object.get('folder', '0')}']/file[@id='{mainline_timeline_key_object.get('file', '0')}']")

                    for key in keys:
                        key_object = key.find("object")
                        key_file = scml_root.find(f"folder[@id='{key_object.get('folder', '0')}']/file[@id='{key_object.get('file', '0')}']")

                        mainline_object_ref = mainline.find(f".//object_ref[@timeline='{timeline_id}'][@key='{key.get('id', '0')}']")
                        # mainline_file = scml.find(f"folder[@id='{mainline_object_ref.get('folder', '0')}']/file[@id='{mainline_object_ref.get('file', '0')}']")

                        # first_key - key
                        adjusted_pivot_x = float(key_object.get("pivot_x") or mainline_file.get("pivot_x", "0")) - float(key_file.get("pivot_x", "0"))
                        adjusted_pivot_y = float(key_object.get("pivot_y") or mainline_file.get("pivot_y", "0")) - float(key_file.get("pivot_y", "0"))

                        key_object.set("key_file", key_file)
                        key_object.set("adjusted_pivot_x", adjusted_pivot_x)
                        key_object.set("adjusted_pivot_y", adjusted_pivot_y)
                        key_object.set("mainline_object_ref", mainline_object_ref)

                for frame_num in range(0, anim_length // frame_duration):
                    # convert_bone_keys_to_frames
                    frame_num = frame_num * frame_duration
                    flattened_bone_frames = []
                    for timeline in bone_timelines:
                        bone_keys = timeline.findall("key")
                        bone_key_count = len(bone_keys)

                        if bone_key_count <= 0:
                            continue

                        start_key, end_key, start_time, end_time, blend = get_key_blend(bone_keys, anim_length, frame_num, looping)
                        if end_time - start_time >= 1.5 * frame_duration:
                            continue

                        start_key_bone = start_key.find("bone")
                        end_key_bone = end_key.find("bone")

                        start_key_bone_spin = int(end_key.get("spin", 1))

                        bone_frame = {}
                        bone_frame["x"] = lerp(float(start_key_bone.get("x", 0)), float(end_key_bone.get("x", 0)), blend)
                        bone_frame["y"] = lerp(float(start_key_bone.get("y", 0)), float(end_key_bone.get("y", 0)), blend)
                        bone_frame["scale_x"] = lerp(float(start_key_bone.get("scale_x", 1)), float(end_key_bone.get("scale_x", 1)), blend)
                        bone_frame["scale_y"] = lerp(float(start_key_bone.get("scale_y", 1)), float(end_key_bone.get("scale_y", 1)), blend)
                        bone_frame["angle"] = lerp_angle(float(start_key_bone.get("angle", 0)), float(end_key_bone.get("angle", 0)), blend, start_key_bone_spin)

                        bone_ref_match = mainline.find(f"key/bone_ref[@timeline='{timeline.get('id', 0)}']")
                        assert bone_ref_match != None, f"ERROR: Bone {timeline.get('name')} doesn't exist on first key frame."

                        bone_frame["id"] = int(bone_ref_match.get("id", -1))
                        bone_frame["parent_id"] = int(bone_ref_match.get("parent", -1))

                        bone_frame["spin"] = start_key_bone_spin
                        bone_frame["time"] = frame_num

                        bone_frame["flattened"] = create_trans_rot_scale_pivot_matrix(
                            (bone_frame["x"], bone_frame["y"]),
                            bone_frame["angle"],
                            (bone_frame["scale_x"], bone_frame["scale_y"]),
                            (0, 0),
                        )
                        bone_frame["is_flattened"] = False
                        flattened_bone_frames.append(bone_frame)

                    # flatten bones
                    for bone_frame in flattened_bone_frames:
                        flatten(bone_frame, flattened_bone_frames)

                    uninitialized_rect = True
                    bounding_rectangle = {"x1": float("-inf"), "x2": float("-inf"), "y1": float("-inf"), "y2": float("-inf")}
                    Frame = {"elements": []}
                    for timeline_idx, timeline in enumerate(valid_timelines):
                        keys = timeline.findall("key")
                        keys = keys[:-1] if len(keys) > 1 else keys
                        key_count = len(keys)

                        should_export = True
                        if key_count <= 0 or (time_max_key.find(f"object_ref[@timeline='{timeline_idx}'][@key='0']") == None and int(keys[0].get("time", 0)) > frame_num):
                            should_export = False
                        elif time_max_key.find(f"object_ref[@timeline='{key_count - 1}'][@key='{key_count - 1}']") == None and int(keys[key_count - 1].get("time", 0)) + frame_duration < frame_num:
                            should_export = looping

                        if not should_export:
                            continue

                        start_key, end_key, start_time, end_time, blend = get_key_blend(keys, anim_length, frame_num, looping)
                        if end_time - start_time >= 1.5 * frame_duration and key_count > 1:
                            continue

                        start_key_object = start_key.find("object")
                        end_key_object = end_key.find("object")

                        start_key_object_file = start_key_object.get("key_file")
                        end_key_object_file = end_key_object.get("key_file")

                        start_angle = float(start_key_object.get("angle", 0))
                        end_angle = float(end_key_object.get("angle", 0))
                        end_spin = int(end_key.get("spin", 1))

                        start_alpha = float(start_key_object.get("a", 1))
                        end_alpha = float(end_key_object.get("a", 1))

                        frame_position_x = lerp(float(start_key_object.get("x", 0)), float(end_key_object.get("x", 0)), blend)
                        frame_position_y = lerp(float(start_key_object.get("y", 0)), float(end_key_object.get("y", 0)), blend)

                        frame_dimension_x, frame_dimension_y = int(start_key_object_file.get("width", 0)), int(start_key_object_file.get("height", 0))
                        frame_pivot_x = lerp(start_key_object.get("adjusted_pivot_x"), end_key_object.get("adjusted_pivot_x"), blend)
                        frame_pivot_y = lerp(start_key_object.get("adjusted_pivot_y"), end_key_object.get("adjusted_pivot_y"), blend)
                        frame_scale_x = lerp(float(start_key_object.get("scale_x", 1)), float(end_key_object.get("scale_x", 1)), blend)
                        frame_scale_y = lerp(float(end_key_object.get("scale_y", 1)), float(end_key_object.get("scale_y", 1)), blend)
                        frame_angle = lerp_angle(start_angle, end_angle, blend, end_spin)
                        frame_alpha = lerp(start_alpha, end_alpha, blend)

                        timeline_frame_name = scml_root.find(f"folder[@id='{start_key_object.get('folder', '0')}']").get("name")
                        timeline_frame_parent_id = start_key_object.get("parent", -1)
                        timeline_layer_name = re.sub(r"_\d+$", "", timeline.get("name", ""))
                        timeline_frame_num = int(start_key_object.get("file", "0"))

                        # apply bones to frames
                        mainline_object_ref = start_key_object.get("mainline_object_ref")
                        parent_id = int(mainline_object_ref.get("parent", -1))
                        if parent_id >= 0:
                            for bone_frame in flattened_bone_frames:
                                if bone_frame["id"] == parent_id:
                                    frame_scale_x, frame_scale_y = frame_scale_x * bone_frame["scale_x"], frame_scale_y * bone_frame["scale_x"]

                                    sx, sy = frame_position_x * bone_frame["scale_x"], frame_position_y * bone_frame["scale_y"]

                                    s, c = math.sin(bone_frame["angle"]), math.cos(bone_frame["angle"])

                                    frame_position_x = bone_frame["x"] + sx * c - sy * s
                                    frame_position_y = bone_frame["y"] + sx * s + sy * c

                                    frame_angle += bone_frame["angle"]

                        scaled_pivot_x, scaled_pivot_y = frame_pivot_x * frame_dimension_x, frame_pivot_y * frame_dimension_y

                        matrix = create_trans_rot_scale_pivot_matrix((frame_position_x, -frame_position_y), frame_angle, (frame_scale_x, frame_scale_y), (-scaled_pivot_x, scaled_pivot_y))
                        m = matrix.matrix

                        z_index = valid_timelines_cont - int(mainline_object_ref.get("z_index", 0))
                        Frame["elements"].append({"name": timeline_frame_name, "frame": timeline_frame_num, "layername": timeline_layer_name, "m_a": m[0][0], "m_b": m[1][0], "m_c": m[0][1], "m_d": m[1][1], "m_tx": m[0][2], "m_ty": m[1][2], "z_index": z_index})

                        # export animations
                        folder = scml_root.find(f".//folder[@name='{timeline_frame_name}']")
                        result = extend_bounding_box(folder, timeline_frame_name, uninitialized_rect, bounding_rectangle, timeline_frame_num, matrix)
                        if result:
                            uninitialized_rect = False

                    x, y = bounding_rectangle["x1"], bounding_rectangle["y1"]
                    w, h = math.ceil(bounding_rectangle["x2"] - bounding_rectangle["x1"]), math.ceil(bounding_rectangle["y2"] - bounding_rectangle["x1"])
                    w, h = math.ceil(w * 1.1), math.ceil(h * 1.1)

                    Frame["x"] = x
                    Frame["y"] = y
                    Frame["w"] = w
                    Frame["h"] = h

                    anim_data["banks"][entity.get("name")][animation.get("name")]["frames"].append(Frame)

        from ds_file.anim import DSAnim
        with DSAnim(anim_data) as anim:
            anim.parse_file(build_data, symbols_images)
            anim.save_bin(output)
