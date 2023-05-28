# encoding:utf-8

"""
wechaty channel
Python Wechaty - https://github.com/wechaty/python-wechaty
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import os
import time
import asyncio
from bridge.context import Context
from wechaty_puppet import FileBox
from wechaty import Wechaty, Contact
from wechaty.user import Message
from bridge.reply import *
from bridge.context import *
from channel.chat_channel import ChatChannel
from channel.wechat.wechaty_message import WechatyMessage
from common.log import logger
from config import conf
try:
    from voice.audio_convert import mp3_to_sil
except Exception as e:
    pass

thread_pool = ThreadPoolExecutor(max_workers=8)
def thread_pool_callback(worker):
    worker_exception = worker.exception()
    if worker_exception:
        logger.exception("Worker return exception: {}".format(worker_exception))
class WechatyChannel(ChatChannel):

    def __init__(self):
        pass

    def startup(self):
        asyncio.run(self.main())

    async def main(self):
        config = conf()
        token = config.get('wechaty_puppet_service_token')
        os.environ['WECHATY_PUPPET_SERVICE_TOKEN'] = token
        os.environ['WECHATY_LOG']="warn"
        # os.environ['WECHATY_PUPPET_SERVICE_ENDPOINT'] = '127.0.0.1:9001'
        self.bot = Wechaty()
        self.bot.on('login', self.on_login)
        self.bot.on('message', self.on_message)
        await self.bot.start()

    async def on_login(self, contact: Contact):
        self.user_id = contact.contact_id
        self.name = contact.name
        logger.info('[WX] login user={}'.format(contact))

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        receiver_id = context['receiver']
        loop = asyncio.get_event_loop()
        if context['isgroup']:
            receiver = asyncio.run_coroutine_threadsafe(self.bot.Room.find(receiver_id),loop).result()
        else:
            receiver = asyncio.run_coroutine_threadsafe(self.bot.Contact.find(receiver_id),loop).result()
        msg = None
        if reply.type == ReplyType.TEXT:
            msg = reply.content
            asyncio.run_coroutine_threadsafe(receiver.say(msg),loop).result()
            logger.info('[WX] sendMsg={}, receiver={}'.format(reply, receiver))
        elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
            msg = reply.content
            asyncio.run_coroutine_threadsafe(receiver.say(msg),loop).result()
            logger.info('[WX] sendMsg={}, receiver={}'.format(reply, receiver))
        elif reply.type == ReplyType.VOICE:
            voiceLength = None
            if reply.content.endswith('.mp3'):
                mp3_file = reply.content
                sil_file = os.path.splitext(mp3_file)[0] + '.sil'
                voiceLength = mp3_to_sil(mp3_file, sil_file)
                try:
                    os.remove(mp3_file)
                except Exception as e:
                    pass
            elif reply.content.endswith('.sil'):
                sil_file = reply.content
            else:
                raise Exception('voice file must be mp3 or sil format')
            # 发送语音
            t = int(time.time())
            msg = FileBox.from_file(sil_file, name=str(t) + '.sil')
            if voiceLength is not None:
                msg.metadata['voiceLength'] = voiceLength
            asyncio.run_coroutine_threadsafe(receiver.say(msg),loop).result()
            try:
                os.remove(sil_file)
            except Exception as e:
                pass
            logger.info('[WX] sendVoice={}, receiver={}'.format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL: # 从网络下载图片
            img_url = reply.content
            t = int(time.time())
            msg = FileBox.from_url(url=img_url, name=str(t) + '.png')
            asyncio.run_coroutine_threadsafe(receiver.say(msg),loop).result()
            logger.info('[WX] sendImage url={}, receiver={}'.format(img_url,receiver))
        elif reply.type == ReplyType.IMAGE: # 从文件读取图片
            image_storage = reply.content
            image_storage.seek(0)
            t = int(time.time())
            msg = FileBox.from_base64(base64.b64encode(image_storage.read()), str(t) + '.png')
            asyncio.run_coroutine_threadsafe(receiver.say(msg),loop).result()
            logger.info('[WX] sendImage, receiver={}'.format(receiver))

    async def on_message(self, msg: Message):
        """
        listen for message event
        """
        try:
            cmsg = await WechatyMessage(msg)
        except NotImplementedError as e:
            logger.debug('[WX] {}'.format(e))
            return
        except Exception as e:
            logger.exception('[WX] {}'.format(e))
            return
        logger.debug('[WX] message:{}'.format(cmsg))
        room = msg.room()  # 获取消息来自的群聊. 如果消息不是来自群聊, 则返回None
        
        isgroup = room is not None
        ctype = cmsg.ctype
        context = self._compose_context(ctype, cmsg.content, isgroup=isgroup, msg=cmsg)
        if context:
            logger.info('[WX] receiveMsg={}, context={}'.format(cmsg, context))
            thread_pool.submit(self._handle_loop, context, asyncio.get_event_loop()).add_done_callback(thread_pool_callback)

    def _handle_loop(self,context,loop):
        asyncio.set_event_loop(loop)
        self._handle(context)