import tornado.gen as gen
import subprocess

from base import NetLayer

class LineBufferLayer(NetLayer):
    # Buffers incoming data line-by-line
    NAME = "linebuffer"
    CONN_ID_KEY = "tcp_conn"

    def __init__(self, *args, **kwargs):
        super(LineBufferLayer, self).__init__(*args, **kwargs)
        self.buffers = {}
        self.enabled = {}
        self.closed = {}
        
    @gen.coroutine
    def on_read(self, src, header, data):
        conn_id = header[self.CONN_ID_KEY]
        if conn_id not in self.buffers:
            self.buffers[conn_id] = {0: "", 1: ""}
            self.enabled[conn_id] = {0: True, 1: True}
            self.closed[conn_id] = {0: False, 1: False}

        def lbl_enable(s):
            self.enabled[conn_id][s] = True
        def lbl_disable(s):
            self.enabled[conn_id][s] = False

        header["lbl_enable"] = lbl_enable
        header["lbl_disable"] = lbl_disable

        if data is None:
            buff = self.buffers[conn_id][src]
            self.buffers[conn_id][src] = ""
            yield self.bubble(src, header, buff)
        else:
            #print "> recvd data from ", src, len(data), len(self.buffers[conn_id][src])
            self.buffers[conn_id][src] += data
            if self.enabled[conn_id][src]:
                while '\n' in self.buffers[conn_id][src]:
                    line, _newline, self.buffers[conn_id][src] = self.buffers[conn_id][src].partition('\n')
                    #print ">>>", line1460
                    yield self.bubble(src, header, line + "\n")

            if not self.enabled[conn_id][src]:
                buff = self.buffers[conn_id][src]
                self.buffers[conn_id][src] = ""
                yield self.bubble(src, header, buff)

    @gen.coroutine
    def on_close(self, src, header):
        conn_id = header[self.CONN_ID_KEY]
        if conn_id in self.buffers:
            buff = self.buffers[conn_id][src]
            self.buffers[conn_id][src] = ""
            yield self.bubble(src, header, buff)

            self.closed[conn_id][src] = True
            if all(self.closed[conn_id]): 
                del self.closed[conn_id]
                del self.enabled[conn_id]
                del self.buffers[conn_id]
        yield self.close_bubble(src, header)

class MultiOrderedDict(list):
    def __init__(self, from_list=None):
        self.d = {}
        if from_list is not None:
            for (k, v) in from_list:
                self.push(k, v)

    def remove(self, key):
        key = key.lower()
        if key in self.d:
            print "Removing", key, ":", self.d[key]
            del self.d[key]
            for (i, (k, v)) in enumerate(self):
                if k.lower() == key:
                    self.pop(i)

    def first(self, key, default=None):
        key = key.lower()
        if key in self.d:
            return self.d[key][0]
        return default

    def last(self, key, default=None):
        key = key.lower()
        if key in self.d:
            return self.d[key][-1]
        return default

    def push(self, key, value):
        self.append((key, value))
        key = key.lower()
        if key in self.d:
            self.d[key].append(value)
        else:
            self.d[key] = [value]

    def last_value_append(self, new_part):
        # This is very specific to HTTP header parsing
        # Append a string to the last updated value
        old_key, old_value = self[-1][0]
        new_value = old_value + new_part

        self[-1] = (old_key, new_value)
        self.d[old_key.lower()][-1] = new_value

        return key, new_value

    def __contains__(self, key):
        return key.lower() in self.d

    def set(self, key, new_value, index=0):
        j = 0
        key = key.lower()
        for i, (k, v) in enumerate(self):
            if k.lower() == key:
                if j == index:
                    self[i] = (k, new_value)
                    break
                j += 1
        else:
            self.push(key, new_value)
        try:
            self.d[key][index] = new_value
        except IndexError:
            self.d[key][-1] = new_value

class PrintLayer(NetLayer):
    NAME = "print"

    # coroutine
    def write(self, dst, header, payload):
        print ">", payload
        return self.write_back(dst, header, payload)

    # coroutine
    def on_read(self, src, header, payload):
        print "<", payload
        return self.bubble(src, header, payload)    

class RecorderLayer(NetLayer):
    NAME = "recorder"

    def __init__(self):
        super(RecorderLayer, self).__init__()
        self.f = None

    def do_start(self, filename):
        self.f = open(filename, "w")
        self.byte_counter = 0
        self.packet_counter = 0

    def do_stop(self):
        if not self.f:
            raise Exception("Not recording!")
        self.f.close()
        self.f = None
        print "Recorded {0} packets ({1} bytes)".format(self.packet_counter, self.byte_counter)

    # corountine
    def on_read(self, src, header, payload):
        if self.f:
            self.f.write(payload)
            self.byte_counter += len(payload)
            self.packet_counter += 1
        return self.bubble(src, header, payload)

class PipeLayer(NetLayer):
    NAME = "pipe"
    COMMAND = ["cat", "-"]
    CONN_ID_KEY = "tcp_conn"
    
    def __init__(self):
        super(PipeLayer, self).__init__()
        self.sps = {}
        self.debug = False

    @gen.coroutine
    def write(self, dst, header, payload):
        if self.debug:
            print "PIPE>", len(payload)
        conn_id = header[self.CONN_ID_KEY]
        if conn_id not in self.sps:
            self.sps[conn_id] = subprocess.Popen(self.COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        #self.sps[conn_id].stdin.write(payload)
        output, _stderr = self.sps[conn_id].communicate(input=payload)
        if self.debug:
            print "Pipe stderr: ", _stderr
            print "PIPE<", len(output)
        del self.sps[conn_id]
        return self.write_back(dst, header, output)

    @gen.coroutine
    def on_close(self, src, header):
        conn_id = header[self.CONN_ID_KEY]
        if conn_id not in self.sps:
            return

        self.sps[conn_id].stdin.close()
        output = self.sps[conn_id].communicate()
        self.sps[conn_id].kill()
        del self.sps[conn_id]

        yield self.passthru(src, header, output)

