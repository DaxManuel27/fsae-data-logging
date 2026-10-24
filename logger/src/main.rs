//! v1 CAN logger: read every frame from CHANNEL, append 24-byte records to one file per run.

use std::fs::{self, File};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use socketcan::{CanFrame, CanSocket, EmbeddedFrame, Frame, Socket};

// ---- Settings ----
const CHANNEL: &str = "vcan0"; // "can0" on the Pi
const OUTPUT_DIR: &str = "logs";
const FLUSH_INTERVAL: Duration = Duration::from_millis(100);
const READ_TIMEOUT: Duration = Duration::from_millis(100); // lets the loop see Ctrl-C and flush on a quiet bus

// ---- Record layout (little-endian) ----
// [0..8]   u64 timestamp, ns since Unix epoch
// [8..12]  u32 CAN ID incl. kernel EFF/RTR/ERR flag bits
// [12]     u8  DLC
// [13..16] padding (zero)
// [16..24] data, zero-filled past DLC
const RECORD_SIZE: usize = 24;

fn main() -> io::Result<()> {
    let running = install_ctrlc_handler();
    let socket = open_socket(CHANNEL)?;
    let (path, mut out) = create_run_file(Path::new(OUTPUT_DIR))?;
    println!("logging {CHANNEL} -> {}", path.display());

    let mut frames: u64 = 0;
    let mut last_flush = Instant::now();

    while running.load(Ordering::Relaxed) {
        match socket.read_frame() {
            Ok(frame) => {
                out.write_all(&encode_record(now_ns(), &frame))?;
                frames += 1;
            }
            Err(e) if is_timeout(&e) => {} // nothing arrived, fall through to flush check
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {} // signal arrived mid-read
            Err(e) => return Err(e),
        }
        if last_flush.elapsed() >= FLUSH_INTERVAL {
            out.flush()?;
            last_flush = Instant::now();
        }
    }

    out.flush()?;
    out.get_ref().sync_all()?;
    println!("stopped: {frames} frames written to {}", path.display());
    Ok(())
}

/// Sets a flag to false on Ctrl-C / SIGTERM so the loop exits cleanly.
fn install_ctrlc_handler() -> Arc<AtomicBool> {
    let running = Arc::new(AtomicBool::new(true));
    let flag = running.clone();
    ctrlc::set_handler(move || flag.store(false, Ordering::Relaxed))
        .expect("failed to install Ctrl-C handler");
    running
}

/// Opens a raw CAN socket on `channel` with READ_TIMEOUT set.
fn open_socket(channel: &str) -> io::Result<CanSocket> {
    let socket = CanSocket::open(channel)?;
    socket.set_read_timeout(READ_TIMEOUT)?;
    Ok(socket)
}

/// Creates OUTPUT_DIR if needed and a new file named can_YYYYMMDD_HHMMSS.bin.
fn create_run_file(dir: &Path) -> io::Result<(PathBuf, BufWriter<File>)> {
    fs::create_dir_all(dir)?;
    let name = chrono::Local::now().format("can_%Y%m%d_%H%M%S.bin").to_string();
    let path = dir.join(name);
    // create_new: fail rather than overwrite an existing run
    let file = File::options().write(true).create_new(true).open(&path)?;
    Ok((path, BufWriter::new(file)))
}

/// Current wall-clock time in ns since the Unix epoch.
fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before 1970")
        .as_nanos() as u64
}

/// Packs one frame into the 24-byte record layout above.
fn encode_record(ts_ns: u64, frame: &CanFrame) -> [u8; RECORD_SIZE] {
    let mut rec = [0u8; RECORD_SIZE];
    let data = frame.data();
    rec[0..8].copy_from_slice(&ts_ns.to_le_bytes());
    rec[8..12].copy_from_slice(&frame.id_word().to_le_bytes());
    rec[12] = frame.dlc() as u8;
    rec[16..16 + data.len()].copy_from_slice(data);
    rec
}

/// A read timeout shows up as WouldBlock or TimedOut depending on platform.
fn is_timeout(e: &io::Error) -> bool {
    matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut)
}

#[cfg(test)]
mod tests {
    use super::*;
    use socketcan::{ExtendedId, StandardId};

    #[test]
    fn encodes_standard_frame() {
        let id = StandardId::new(0x123).unwrap();
        let frame = CanFrame::new(id, &[1, 2, 3, 4, 5]).unwrap();
        let rec = encode_record(0x0102030405060708, &frame);
        assert_eq!(&rec[0..8], &0x0102030405060708u64.to_le_bytes());
        assert_eq!(u32::from_le_bytes(rec[8..12].try_into().unwrap()), 0x123);
        assert_eq!(rec[12], 5);
        assert_eq!(&rec[13..16], &[0, 0, 0]);
        assert_eq!(&rec[16..24], &[1, 2, 3, 4, 5, 0, 0, 0]);
    }

    #[test]
    fn keeps_extended_flag() {
        let id = ExtendedId::new(0x1ABCDE).unwrap();
        let frame = CanFrame::new(id, &[0xFF; 8]).unwrap();
        let rec = encode_record(0, &frame);
        assert_eq!(u32::from_le_bytes(rec[8..12].try_into().unwrap()), 0x8000_0000 | 0x1ABCDE);
        assert_eq!(&rec[16..24], &[0xFF; 8]);
    }
}
