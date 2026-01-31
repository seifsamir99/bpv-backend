const express = require('express');
const cors = require('cors');
const { google } = require('googleapis');

const app = express();
app.use(cors());
app.use(express.json());

// Initialize Google Sheets API
const getAuth = () => {
  const email = process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL;
  let privateKey = process.env.GOOGLE_PRIVATE_KEY;

  if (privateKey) {
    privateKey = privateKey.replace(/\\n/g, '\n');
  }

  const auth = new google.auth.GoogleAuth({
    credentials: {
      client_email: email,
      private_key: privateKey,
    },
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth;
};

const getSheets = async () => {
  const auth = getAuth();
  return google.sheets({ version: 'v4', auth });
};

const SHEET_ID = process.env.GOOGLE_SHEET_ID || '1DZfS3J6Men-XuX11nXAYnMhgQKCn4SJTFDDFxlEQAwk';
const SHEET_NAME = 'BPV';
const ALL_BPV_SHEET = 'all Bpv';

// Build voucher position map
const buildVoucherPositionMap = () => {
  const positions = {};

  const oldFormatBases = [5, 28, 48, 68, 88, 108, 128, 148, 168];
  oldFormatBases.forEach((base, i) => {
    positions[i + 1] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'old' };
  });

  const transitional = [[10, 194], [11, 221], [12, 241], [13, 261], [14, 287], [15, 313], [16, 338], [17, 366]];
  transitional.forEach(([bpvNum, base]) => {
    positions[bpvNum] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'transitional' };
  });

  for (let i = 0; i < 43; i++) {
    const base = 393 + (i * 27);
    positions[18 + i] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'new' };
  }

  return positions;
};

const VOUCHER_POSITIONS = buildVoucherPositionMap();

const formatDate = (value) => {
  if (!value) return '';
  if (typeof value === 'number') {
    const date = new Date((value - 25569) * 86400 * 1000);
    return date.toLocaleDateString('en-GB');
  }
  return String(value);
};

const parseAmount = (value) => {
  if (!value) return 0;
  if (typeof value === 'number') return value;
  return parseFloat(String(value).replace(/,/g, '')) || 0;
};

const getCell = (rows, rowIdx, colIdx) => {
  if (rowIdx < rows.length && colIdx < rows[rowIdx].length) return rows[rowIdx][colIdx];
  return '';
};

const parseVoucherAtPosition = (rows, bpvNum, pos) => {
  const bpvNoRow = pos.bpvNoRow - 1;
  const dateRow = pos.dateRow - 1;
  const dataRow = pos.dataRow - 1;
  const totalRow = pos.totalRow - 1;

  const bpvNo = getCell(rows, bpvNoRow, 3);
  const date = getCell(rows, dateRow, 3);
  const pdcType = getCell(rows, bpvNoRow, 4) || 'PDC';

  const lineItems = [];
  const skipLabels = ['TOTAL AMOUNT', 'Prepared By', 'Received By', 'Approved By', 'Checked By', '___'];

  for (let i = 0; i < 5; i++) {
    const itemRow = dataRow + i;
    const description = getCell(rows, itemRow, 1);
    if (skipLabels.some(label => String(description).includes(label))) continue;

    const debit = getCell(rows, itemRow, 5);
    const credit = getCell(rows, itemRow, 6);
    const companyName = getCell(rows, itemRow, 2);

    if ((Boolean(companyName) || Boolean(description)) && (parseAmount(debit) > 0 || parseAmount(credit) > 0)) {
      lineItems.push({
        srNo: getCell(rows, itemRow, 0) || String(lineItems.length + 1),
        description, companyName,
        chequeNo: getCell(rows, itemRow, 3),
        chequeDate: formatDate(getCell(rows, itemRow, 4)),
        debit: parseAmount(debit),
        credit: parseAmount(credit)
      });
    }
  }

  const totalAmount = parseAmount(getCell(rows, totalRow, 2));
  return {
    id: bpvNum, bpvNo, date: formatDate(date), pdcType, lineItems, totalAmount,
    hasData: lineItems.length > 0 || totalAmount > 0, baseRow: pos.dataRow, format: pos.format
  };
};

// API Routes
app.get('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {
        console.error(`Error parsing BPV #${bpvNumStr}:`, error);
      }
    }

    res.json({ success: true, data: vouchers });
  } catch (error) {
    console.error('Error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/next-number', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {}
    }

    const bpvNumbers = vouchers.map(v => parseInt(v.bpvNo)).filter(n => !isNaN(n));
    const nextNumber = bpvNumbers.length > 0 ? Math.max(...bpvNumbers) + 1 : 1;

    res.json({ success: true, data: { nextNumber } });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];

    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const startRow = pos.bpvNoRow - 5;
    const endRow = pos.totalRow + 10;

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A${startRow}:G${endRow}`,
    });

    const rows = response.data.values || [];
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1,
      dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1,
      totalRow: pos.totalRow - startRow + 1,
      format: pos.format
    };

    const voucher = parseVoucherAtPosition(rows, bpvNum, adjustedPos);
    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

const calculateTotal = (lineItems) => {
  return Math.round((lineItems || []).reduce((sum, item) => sum + (parseAmount(item.debit) || 0), 0) * 100) / 100;
};

const syncToAllBpv = async (bpvNum, voucherData) => {
  const sheets = await getSheets();
  const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
  const rows = response.data.values || [];

  let targetRow = -1;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) { targetRow = i + 1; break; }
  }

  const firstItem = voucherData.lineItems?.[0] || {};
  const rowData = [[
    voucherData.bpvNo || bpvNum, firstItem.companyName || '', firstItem.description || '',
    voucherData.date || '', firstItem.chequeNo || '', firstItem.chequeDate || '',
    voucherData.totalAmount || '', voucherData.pdcType || 'PDC'
  ]];

  if (targetRow > 0) {
    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${targetRow}:H${targetRow}`,
      valueInputOption: 'USER_ENTERED', requestBody: { values: rowData }
    });
  } else {
    await sheets.spreadsheets.values.append({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:H`,
      valueInputOption: 'USER_ENTERED', insertDataOption: 'INSERT_ROWS', requestBody: { values: rowData }
    });
  }
};

app.post('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A1:G2000` });
    const rows = response.data.values || [];

    // Find empty slot
    const usedSlots = new Set();
    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
      if (voucher && voucher.hasData) usedSlots.add(parseInt(bpvNumStr));
    }

    let emptySlot = null;
    for (const bpvNum of Object.keys(VOUCHER_POSITIONS).map(k => parseInt(k)).sort((a, b) => a - b)) {
      if (!usedSlots.has(bpvNum)) { emptySlot = bpvNum; break; }
    }

    if (!emptySlot) return res.status(400).json({ success: false, error: 'No empty slots available' });

    // Update the slot
    const pos = VOUCHER_POSITIONS[emptySlot];
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    const total = data.totalAmount !== undefined ? data.totalAmount : calculateTotal(data.lineItems);
    requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[total]] });

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher and sync
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.bpvNoRow - 5}:G${pos.totalRow + 10}` });
    const adjustedPos = { bpvNoRow: 6, dateRow: 7, dataRow: 13, totalRow: 15, format: pos.format };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], emptySlot, adjustedPos);

    await syncToAllBpv(emptySlot, voucher);

    res.status(201).json({ success: true, data: voucher });
  } catch (error) {
    console.error('Create error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.put('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    if (data.totalAmount !== undefined) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[data.totalAmount]] });
    } else if (data.lineItems) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[calculateTotal(data.lineItems)]] });
    }

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher
    const startRow = pos.bpvNoRow - 5;
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${startRow}:G${pos.totalRow + 10}` });
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1, dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1, totalRow: pos.totalRow - startRow + 1, format: pos.format
    };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], bpvNum, adjustedPos);

    await syncToAllBpv(bpvNum, voucher);

    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.delete('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.bpvNoRow}:E${pos.bpvNoRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['', '']] }
    });

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.dateRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    for (let i = 0; i < 5; i++) {
      await sheets.spreadsheets.values.update({
        spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
        valueInputOption: 'RAW', requestBody: { values: [[String(i + 1), '', '', '', '', '', '']] }
      });
    }

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!C${pos.totalRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    // Clear from all Bpv sheet
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
    const rows = response.data.values || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'RAW', requestBody: { values: [['', '', '', '', '', '', '', '']] }
        });
        break;
      }
    }

    res.json({ success: true, message: `Voucher #${bpvNum} deleted` });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

// Health check
app.get('/health', (req, res) => res.json({ status: 'ok' }));

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`BPV Backend running on port ${PORT}`));
