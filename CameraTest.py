from pythonosc import dispatcher, osc_server
import time

OSC_IP = "127.0.0.1"
OSC_PORT_RECEIVE = 9001

current_pose = {"pos": (0, 0, 0), "rot": (0, 0, 0)}

def on_usercamera_pose(address, *args):
    if len(args) >= 6:
        pos = tuple(round(float(a), 3) for a in args[:3])
        rot = tuple(round(float(a), 2) for a in args[3:6])
        current_pose["pos"] = pos
        current_pose["rot"] = rot
        print(f"Camera Position: {pos} | Rotation: {rot}")


def start_osc_server():
    disp = dispatcher.Dispatcher()
    disp.map("/usercamera/Pose", on_usercamera_pose)

    server = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT_RECEIVE), disp)
    print(f"Listening for /usercamera/Pose on {OSC_IP}:{OSC_PORT_RECEIVE} ...")
    server.serve_forever()


if __name__ == "__main__":
    start_osc_server()
