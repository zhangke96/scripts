"""
0. 程序启动之后就开始使用mpstat抓取系统的CPU情况
1. 根据进程关键字查询进程信息, 发现进程重启之后开始使用perf抓取
2. 抓取每个子线程的一些信息,thread_id, state, last_cpu, switch_cnt, kernel stack, schedstat
"""
import os, sys, time, subprocess, logging, signal, atexit
from re import sub
from multiprocessing import Process

# 抓取进程信息周期 秒
CAPTURE_INTERNAL = 0.5
exited = False

def exit_perf(signum, frame):
  global exited
  if exited == True:
    exit(0)
  exited = True
  global mpstat_pipe
  try:
    if mpstat_pipe:
      os.killpg(os.getpgid(mpstat_pipe.pid), signal.SIGTERM)
      logging.info("mpstat log:%s", mpstat_log)
      mpstat_pipe = None
  except:
    pass

  global perf_pipe
  try:
    if perf_pipe:
      stop_perf()
  except:
    pass
  exit(0)

def exit_perf2():
  exit_perf(None, None)

atexit.register(exit_perf2)

def stop_perf():
  global perf_log
  pids = get_process_id(perf_log)
  for pid in pids:
    logging.info("kill perf pid:%s", str(pid))
    # perf进程退出需要使用SIGINT(ctrl+c)
    ret, output = execute_system_command("kill -s INT %s" % (str(pid),))
    logging.info("kill perf ret:%d, stdout:%s, stderr:%s", ret, str(output[0]), str(output[1]))
  # 等待进程退出
  while True:
    pids = get_process_id(perf_log)
    if len(pids) == 0:
      logging.info("not found perf process")
      break
    else:
      logging.info("still have perf process:%s", str(pids))
      time.sleep(1)
  
def config_logging():
  logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s: %(message)s")
  # 日志输出到文件同时输出到屏幕
  log_file = os.path.join("%d_perf.log" % (int(time.time())))
  if os.path.exists(log_file):
    os.rename(log_file, log_file+".bak")
  logging.info("log file:%s", log_file)
  file_handler = logging.FileHandler(log_file)
  file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
  logging.root.addHandler(file_handler)

def Usage():
  print(sys.argv[0], "(process key word) [capture time]")

def execute_system_command(cmd):
  pipe = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  output = pipe.communicate()
  status = pipe.returncode
  return status, output

def start_mpstat():
  global mpstat_log
  mpstat_log = "%d_mpstat.log" % (int(time.time()),)
  cmd = "mpstat -P ALL 1 > %s" % (mpstat_log,)
  pipe = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  logging.info("start run mpstat, log_file:%s", mpstat_log)
  return pipe

def start_perf(pid):
  global perf_log
  perf_log = "%s_perf.data" % (str(pid),)
  global perf_cmd
  perf_cmd = "perf record -F 99 -p %s -g -o %s" % (str(pid), perf_log)
  pipe = subprocess.Popen(perf_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  logging.info("start perf record %s to %s", str(pid), perf_log)
  return pipe

def get_process_id(keyword, jump_my_pid=False):
  ps_cmd = ""
  if jump_my_pid:
    my_pid = os.getpid()
    ps_cmd = "ps -ef |grep '%s' |grep -v grep |grep -v %s" % (keyword, str(my_pid))
  else:
     ps_cmd = "ps -ef |grep '%s' |grep -v grep" % (keyword,)
  ret, output = execute_system_command(ps_cmd)
  result = []
  if ret != 0:
    stderr = str(output[1])
    logging.warning("get process keyword:%s fail, ret:%d, maybe not exist, stderr:%s",
                    keyword, ret, stderr)
    return result
  else:
    for line in str(output[0], encoding='utf-8').split('\n'):
      fields = line.split()
      if len(fields) < 2:
        continue
      else:
        result.append(int(fields[1]))
    return result

def read_file(path):
  try:
    with open(path, 'r') as f:
      return True, f.readlines()
  except Exception as e:
    logging.warning("fail to read file:%s path, exception:%s", path, str(e))
    return False, []

if __name__ == '__main__':
  if len(sys.argv[1:]) < 1:
    Usage()
    exit(1)
  keyword = sys.argv[1]
  capture_time = -1
  if len(sys.argv[1:]) > 1:
    capture_time = int(sys.argv[2])
  config_logging()
  signal.signal(signal.SIGTERM, exit_perf)
  signal.signal(signal.SIGINT, exit_perf)
  # 1. 开始mpstat
  mpstat_pipe = start_mpstat()
  # 2. 检查进程直到进程重启
  before_pid = -1
  pids = get_process_id(keyword, True)
  if len(pids) == 0:
    logging.info("not found process, %s", keyword)
    # 等待进程启动
  elif len(pids) > 1:
    logging.error("found more process:%s with keyword:%s", str(pids), keyword)
    exit(1)
  else:
    before_pid = pids[0]
    logging.info("find process:%d keyword:%s", before_pid, keyword)
  # 3. 等待进程重启
  now_pid = -1
  while True:
    pids = get_process_id(keyword, True)
    if len(pids) == 0:
      logging.info("not found process, %s", keyword)
      # 等待进程启动
    elif len(pids) > 1:
      logging.error("found more process:%s with keyword:%s", str(pids), keyword)
      exit(1)
    else:
      now_pid = pids[0]
      if now_pid != before_pid:
        logging.info("process restrart, now_pid:%d", now_pid)
        break
      else:
        logging.info("continue check process")
    time.sleep(1)
  # 4, 开始执行perf command
  global perf_pipe
  perf_pipe = start_perf(now_pid)
  # 5, 开始周期抓取进程信息
  PROC_DIR = "/proc/%s/task" % (str(now_pid),)
  start_time = int(time.time())
  while True:
    if capture_time > 0 and int(time.time()) - start_time > capture_time:
      logging.info("exit capture, time:%d", capture_time)
      stop_perf()
      break
    if not os.path.exists(PROC_DIR):
      logging.warning("%s not exists", PROC_DIR)
      exit(1)
    for thread_id in os.listdir(PROC_DIR):
      stat_file = os.path.join(PROC_DIR, thread_id, 'stat')
      ret, stat_content = read_file(stat_file)
      if ret == False:
        continue
      if len(stat_content) < 1:
        logging.warning("Tid:%s stat_file empty", thread_id)
      # get last CPU 39th element
      try:
        last_cpu = stat_content[0].split()[38]
      except Exception as e:
        logging.warning("Tid:%s fail to get last_cpu, exception:%s", thread_id, str(e))
        continue
      status_file = os.path.join(PROC_DIR, thread_id, 'status')
      ret, status_content = read_file(status_file)
      if ret == False:
        continue
      need_field = {'Name', 'State', 'voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches'}
      status_result = {}
      for line in status_content:
        split_ret = line.split(':')
        status_result[split_ret[0].strip()] = split_ret[1].strip()
      logging.info("Tid:%s Name:%s State:%s, Last_CPU:%s vol_switch:%s, novol_switch:%s",
                  thread_id, status_result['Name'], status_result['State'], last_cpu,
                  status_result['voluntary_ctxt_switches'],
                  status_result['nonvoluntary_ctxt_switches'])
      stack_file = os.path.join(PROC_DIR, thread_id, 'stack')
      ret, stack_content = read_file(stack_file)
      if ret == False:
        continue
      if len(stack_content) == 0:
        logging.info("Tid:%s kernel stack empty", thread_id)
      else:
        stack_output = ""
        for line in stack_content:
          stack_output = stack_output + line
        logging.info("Tid:%s kernel stack:\n%s", thread_id, stack_output)
      schedstat_file = os.path.join(PROC_DIR, thread_id, 'schedstat')
      ret, schedstat_content = read_file(schedstat_file)
      if ret == False:
        continue
      if len(schedstat_content) == 0:
        logging.info("Tid:%s schedstate empty", thread_id)
      logging.info("Tid:%s schedstate: %s", thread_id, schedstat_content[0].strip('\n'))
    time.sleep(CAPTURE_INTERNAL)  
  exit(0)

