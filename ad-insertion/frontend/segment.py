#!/usr/bin/python3

from tornado import web, gen
from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor
from zkdata import ZKData
from schedule import Schedule
from os.path import isfile
import traceback
import time
import re
import os

zk_manifest_prefix="/ad-insertion-manifest"
zk_segment_prefix="/ad-insertion-segment"
ad_backoff=str(os.environ["AD_BACKOFF"])

class SegmentHandler(web.RequestHandler):
    def __init__(self, app, request, **kwargs):
        super(SegmentHandler, self).__init__(app, request, **kwargs)
        self._sch=Schedule()
        self.executor=ThreadPoolExecutor()
        self._zk=ZKData()

    def check_origin(self, origin):
        return True

    @run_on_executor
    def _get_segment(self, stream, user, algos):
        stream_base = "/".join(stream.split("/")[:-1])
        segment = stream.split("/")[-1]
        print("stream: "+stream, flush=True)
        print("stream_base: "+stream_base, flush=True)
        print("segment: "+segment, flush=True)

        # Redirect if this is an AD stream
        if stream.find("/adstream/") != -1:
            zk_path=zk_segment_prefix+"/"+stream_base+"/link"
            print("get prefix from "+zk_path, flush=True)
            prefix=self._zk.get(zk_path)
            if not prefix:
                zk_path1=zk_segment_prefix+"/"+stream_base+"/backoff"
                prefix="/adstatic"
                try:
                    print("get backoff "+zk_path1, flush=True)
                    backoff=self._zk.get(zk_path1)
                    print(backoff, flush=True)
                    if int(backoff)>0:
                        self._zk.set(zk_path1,str(int(backoff)-1))
                        return None
                except:
                    print(traceback.format_exc(), flush=True)
            return prefix+"/"+segment

        # get zk data for additional scheduling instruction
        seg_info=self._zk.get(zk_manifest_prefix+"/"+stream_base+"/"+user+"/"+segment)
        if seg_info: 
            # schedule ad
            if "transcode" in seg_info:
                for transcode1 in seg_info["transcode"]:
                    zk_path1=zk_segment_prefix+"/"+"/".join(transcode1["stream"].split("/")[4:-1])+"/backoff"
                    print("set backoff "+zk_path1+" to "+ad_backoff, flush=True)
                    self._zk.set(zk_path1,ad_backoff)
                self._sch.transcode(user, seg_info)

            # schedule analytics
            if "analytics" in seg_info:
                if algos.find("object")>=0:
                    self._sch.analyze(seg_info, "object_detection")
                if algos.find("emotion")>=0:
                    self._sch.analyze(seg_info, "emotion_recognition")
                if algos.find("face")>=0:
                    self._sch.analyze(seg_info, "face_recognition")

            if "analytics" in seg_info or "transcode" in seg_info:
                self._sch.flush()

        # redirect to get the media stream
        return '/intercept/' + stream

    @gen.coroutine
    def get(self):
        stream = self.request.uri.replace("/segment/","")
        user = self.request.headers.get('X-USER')
        if not user: 
            self.set_status(400, "X-USER missing in headers")
            return
        algos = self.request.headers.get('X-ALGO')
        if not algos: 
            self.set_status(400, "X-ALGO missing in headers")

        redirect=yield self._get_segment(stream, user, algos)
        if redirect is None:
            self.set_status(404, "AD not ready")
        else:
            print("X-Accel-Redirect: "+redirect, flush=True)
            self.add_header('X-Accel-Redirect',redirect)
            self.set_status(200,'OK')
