from pathlib import Path
from urllib.request import urlretrieve
from urllib.parse import urljoin
import gzip
import shutil

def download_nasdaq(url, target_dir):
  if not target_dir.exists():
    print('Creating directory.')
    target_dir.mkdir(parents=True) # create a new filefolder
  else:
    print('The directory exists.')

  filename = target_dir/url.split('/')[-1]
 

  if not filename.exists():
    print('Downloading the file...')
    urlretrieve(url, filename)
  else:
    print('The file exist.')

  unzipped = target_dir/( filename.stem+'.bin' ) # filename(without extension)+'.bin'
  if not unzipped.exists():
    print('Unzipping...')
    with gzip.open(filename, 'rb') as f_in: # data has been unzipped
      with open(unzipped, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
  else:
    print('The file is already unpacked.')

  return unzipped
